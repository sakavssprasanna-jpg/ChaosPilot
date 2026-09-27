from datetime import datetime, timezone
import json
import time
from typing import Any, Dict, Optional
import httpx

from sandbox.base import TargetEnvironment
from services.config import LOGS_DIR, PAYMENT_SERVICE_URL
from telemetry.db import get_all_summaries

SNAPSHOTS_DIR = LOGS_DIR / "snapshots"

def get_fault_status() -> Dict[str, Any]:
    """Queries current fault state directly from Payment Service."""
    try:
        with httpx.Client(timeout=1.5) as client:
            resp = client.get(f"{PAYMENT_SERVICE_URL}/fault/status")
            if resp.status_code == 200:
                return resp.json()
            return {"active": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"active": False, "error": str(e)}

def create_incident_snapshot(
    env: TargetEnvironment,
    window_seconds: Optional[float] = 300.0,
    save_to_disk: bool = True
) -> Dict[str, Any]:
    """Captures a complete structured snapshot of the current environment state for SRE evidence."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    
    now = time.time()
    iso_time = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()

    # 1. Health status
    service_health = env.check_all_health()

    # Determine overall status
    is_down = any(h.get("status") in ("down", "error") for h in service_health.values())
    is_degraded = any(h.get("status") in ("degraded", "timeout", "unhealthy") for h in service_health.values())
    overall_status = "down" if is_down else ("degraded" if is_degraded else "healthy")

    # 2. Telemetry Summaries
    metrics_summary = get_all_summaries(window_seconds=window_seconds)

    # 3. Fault Status
    fault_status = get_fault_status()

    # 4. Recent Logs
    recent_logs = {
        svc: env.get_logs(svc, lines=30)
        for svc in ["gateway", "order-service", "payment-service"]
    }

    snapshot = {
        "timestamp": now,
        "timestamp_iso": iso_time,
        "overall_status": overall_status,
        "window_seconds": window_seconds,
        "service_health": service_health,
        "metrics_summary": metrics_summary,
        "fault_status": fault_status,
        "recent_logs": recent_logs
    }

    if save_to_disk:
        filename = f"snapshot_{int(now)}.json"
        filepath = SNAPSHOTS_DIR / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        snapshot["snapshot_file"] = str(filepath)

    return snapshot
