import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional
import httpx
import psutil

from sandbox.base import TargetEnvironment
from services.config import SERVICE_MAP, LOGS_DIR

logger = logging.getLogger("process-sandbox")

PID_FILE = LOGS_DIR / "pids.json"

class ProcessTargetEnvironment(TargetEnvironment):
    """Safe process-based target environment executing real services via Python subprocesses.
    
    Zero shell execution: Direct binary invocation with sys.executable.
    Strict allowlist: Only pre-registered services in SERVICE_MAP may be controlled.
    Persistent PID tracking: Enables detached background execution across CLI commands on Windows.
    """

    ALLOWED_SERVICES = set(SERVICE_MAP.keys())

    def __init__(self):
        self._processes: Dict[str, subprocess.Popen] = {}
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_pid_file()

    def _ensure_pid_file(self):
        if not PID_FILE.exists():
            with open(PID_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f)

    def _read_pids(self) -> Dict[str, int]:
        try:
            if PID_FILE.exists():
                with open(PID_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_pid(self, service_name: str, pid: int):
        pids = self._read_pids()
        pids[service_name] = pid
        with open(PID_FILE, "w", encoding="utf-8") as f:
            json.dump(pids, f, indent=2)

    def _remove_pid(self, service_name: str):
        pids = self._read_pids()
        if service_name in pids:
            del pids[service_name]
            with open(PID_FILE, "w", encoding="utf-8") as f:
                json.dump(pids, f, indent=2)

    def _validate_service(self, service_name: str):
        if service_name not in self.ALLOWED_SERVICES:
            raise ValueError(
                f"Unauthorized service '{service_name}'. "
                f"Allowed services: {list(self.ALLOWED_SERVICES)}"
            )

    def is_running(self, service_name: str) -> bool:
        """Checks if the service subprocess is active and responding."""
        self._validate_service(service_name)
        
        # Check in-memory process handle
        proc = self._processes.get(service_name)
        if proc and proc.poll() is None:
            return True

        # Check persisted PID
        pids = self._read_pids()
        pid = pids.get(service_name)
        if pid and psutil.pid_exists(pid):
            try:
                p = psutil.Process(pid)
                if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Check port listening status
        port = SERVICE_MAP[service_name]["port"]
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr and conn.laddr.port == port and conn.status == "LISTEN":
                    return True
        except Exception:
            pass

        return False

    def start_service(self, service_name: str, wait_healthy: bool = True, timeout: float = 8.0) -> bool:
        """Starts a specific service subprocess detached from the parent CLI console."""
        self._validate_service(service_name)

        if self.is_running(service_name):
            logger.info(f"Service '{service_name}' is already running.")
            return True

        cfg = SERVICE_MAP[service_name]
        log_file_path = LOGS_DIR / f"{service_name}.log"
        log_file = open(log_file_path, "a", encoding="utf-8")

        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            f"{cfg['module']}:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(cfg["port"]),
            "--log-level",
            "info"
        ]

        logger.info(f"Launching service '{service_name}' on port {cfg['port']}...")
        
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=str(LOGS_DIR.parent),
            creationflags=creationflags,
            close_fds=True
        )
        self._processes[service_name] = proc
        self._save_pid(service_name, proc.pid)

        if wait_healthy:
            start_time = time.time()
            url = f"{cfg['url']}{cfg['health_path']}"
            while time.time() - start_time < timeout:
                try:
                    with httpx.Client(timeout=1.0) as client:
                        resp = client.get(url)
                        if resp.status_code in (200, 503):
                            logger.info(f"Service '{service_name}' healthy on port {cfg['port']}.")
                            return True
                except (httpx.ConnectError, httpx.TimeoutException):
                    time.sleep(0.2)
            logger.warning(f"Service '{service_name}' process started (PID {proc.pid}) but did not respond within {timeout}s.")
            return False

        return True

    def stop_service(self, service_name: str, timeout: float = 3.0) -> bool:
        """Gracefully stops a specific service process and terminates all child processes."""
        self._validate_service(service_name)
        cfg = SERVICE_MAP[service_name]
        port = cfg["port"]

        pids = self._read_pids()
        target_pid = pids.get(service_name)
        
        proc = self._processes.pop(service_name, None)
        if proc and not target_pid:
            target_pid = proc.pid

        # Terminate by PID
        if target_pid and psutil.pid_exists(target_pid):
            try:
                parent = psutil.Process(target_pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                parent.terminate()
                _, alive = psutil.wait_procs(children + [parent], timeout=timeout)
                for p in alive:
                    try:
                        p.kill()
                    except Exception:
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Cleanup any remaining process holding the designated port
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr and conn.laddr.port == port and conn.pid:
                    try:
                        p = psutil.Process(conn.pid)
                        p.terminate()
                        p.wait(timeout=1.0)
                    except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                        try:
                            p.kill()
                        except Exception:
                            pass
        except Exception:
            pass

        self._remove_pid(service_name)
        logger.info(f"Service '{service_name}' stopped.")
        return True

    def restart_service(self, service_name: str) -> bool:
        """Safely restarts a service and waits until healthy."""
        logger.info(f"Restarting service '{service_name}'...")
        self.stop_service(service_name)
        time.sleep(0.5)  # Allow socket to clear TIME_WAIT
        return self.start_service(service_name, wait_healthy=True)

    def start_all(self) -> Dict[str, bool]:
        """Starts all services in dependency order: Payment -> Order -> Gateway."""
        results = {}
        order = ["payment-service", "order-service", "gateway"]
        for svc in order:
            results[svc] = self.start_service(svc, wait_healthy=True)
        return results

    def stop_all(self) -> Dict[str, bool]:
        """Stops all running services."""
        results = {}
        for svc in list(self.ALLOWED_SERVICES):
            results[svc] = self.stop_service(svc)
        return results

    def check_health(self, service_name: str) -> Dict[str, Any]:
        """Performs a direct HTTP health probe on a service."""
        self._validate_service(service_name)
        cfg = SERVICE_MAP[service_name]
        url = f"{cfg['url']}{cfg['health_path']}"

        t0 = time.time()
        try:
            with httpx.Client(timeout=1.5) as client:
                resp = client.get(url)
                latency_ms = round((time.time() - t0) * 1000, 2)
                data = resp.json() if resp.status_code == 200 else {}
                return {
                    "service": service_name,
                    "status": data.get("status", "unknown") if resp.status_code == 200 else "unhealthy",
                    "http_status": resp.status_code,
                    "latency_ms": latency_ms,
                    "details": data
                }
        except httpx.ConnectError:
            return {
                "service": service_name,
                "status": "down",
                "http_status": 0,
                "latency_ms": None,
                "error": "Connection refused (service not running)"
            }
        except httpx.TimeoutException:
            return {
                "service": service_name,
                "status": "timeout",
                "http_status": 504,
                "latency_ms": round((time.time() - t0) * 1000, 2),
                "error": "Health probe timed out (> 1.5s)"
            }
        except Exception as e:
            return {
                "service": service_name,
                "status": "error",
                "http_status": 500,
                "latency_ms": None,
                "error": str(e)
            }

    def check_all_health(self) -> Dict[str, Dict[str, Any]]:
        """Checks health across all registered services."""
        return {svc: self.check_health(svc) for svc in self.ALLOWED_SERVICES}

    def get_logs(self, service_name: str, lines: int = 50) -> List[str]:
        """Reads the tail of the service log file."""
        self._validate_service(service_name)
        log_file_path = LOGS_DIR / f"{service_name}.log"

        if not log_file_path.exists():
            return []

        try:
            with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.readlines()
                return [line.rstrip() for line in content[-lines:]]
        except Exception as e:
            logger.error(f"Error reading logs for {service_name}: {e}")
            return [f"Error reading logs: {e}"]
