import time
from typing import Any, Dict
import httpx

from sandbox.process_sandbox import ProcessTargetEnvironment
from services.config import PAYMENT_SERVICE_URL
from telemetry.db import clear_metrics, get_summary
from telemetry.traffic import generate_traffic
from telemetry.snapshot import create_incident_snapshot

def format_phase_summary(phase_name: str, traffic: Dict[str, Any], summary: Dict[str, Any]) -> str:
    """Formats a concise single-line or multi-line summary for an incident phase."""
    total = traffic["total_requests"]
    success = traffic["successful_requests"]
    failed = traffic["failed_requests"]
    err_rate = traffic["error_rate"] * 100
    avg_lat = traffic["avg_latency_ms"]
    p95_lat = traffic["p95_latency_ms"]
    statuses = traffic["status_distribution"]
    return (
        f"{phase_name:<12} | Total: {total:<3} | Success: {success:<3} | Fail: {failed:<3} | "
        f"Err%: {err_rate:>5.1f}% | Avg: {avg_lat:>7.1f}ms | P95: {p95_lat:>7.1f}ms | Codes: {statuses}"
    )

def run_deterministic_incident(
    env: ProcessTargetEnvironment,
    reqs_per_phase: int = 10,
    concurrency: int = 2,
    fault_delay_seconds: float = 3.5
) -> Dict[str, Any]:
    """Runs a complete deterministic 3-phase SRE incident:
    Phase 1: Baseline (Nominal traffic)
    Phase 2: Incident Injection (Payment Timeout fault -> Upstream 504 errors)
    Phase 3: Recovery (Reset/Restart -> Restored nominal performance)
    """
    print("\n" + "=" * 90)
    print(" [ChaosPilot] EXECUTING DETERMINISTIC SRE INCIDENT SCENARIO")
    print("=" * 90)

    # 0. Pre-flight health verification
    print("[Phase 0] Verifying services are running and healthy...")
    health = env.check_all_health()
    for svc, h in health.items():
        if h.get("status") != "healthy":
            print(f"  * Service {svc} is not healthy ({h.get('status')}). Starting/resetting environment...")
            env.restart_service(svc)
    
    # Ensure fault is cleared before baseline
    with httpx.Client(timeout=2.0) as client:
        client.post(f"{PAYMENT_SERVICE_URL}/fault/reset")
    time.sleep(0.5)

    # Phase 1: Baseline
    print(f"\n[Phase 1: Baseline] Generating {reqs_per_phase} nominal checkout requests...")
    t1_start = time.time()
    traffic_baseline = generate_traffic(num_requests=reqs_per_phase, concurrency=concurrency, interval_seconds=0.02)
    summary_baseline = get_summary("gateway", window_seconds=time.time() - t1_start + 1.0)
    print("  -> " + format_phase_summary("BASELINE", traffic_baseline, summary_baseline))

    # Phase 2: Fault Injection
    print(f"\n[Phase 2: Incident] Injecting timeout fault into payment-service ({fault_delay_seconds}s delay)...")
    with httpx.Client(timeout=3.0) as client:
        inject_resp = client.post(
            f"{PAYMENT_SERVICE_URL}/fault/inject",
            json={
                "mode": "timeout",
                "delay_seconds": fault_delay_seconds,
                "description": "Controlled payment-processing timeout fault (artificial processing delay)"
            }
        )
        assert inject_resp.status_code == 200

    print(f"  Generating {reqs_per_phase} checkout requests under active failure conditions...")
    t2_start = time.time()
    traffic_incident = generate_traffic(num_requests=reqs_per_phase, concurrency=concurrency, interval_seconds=0.02)
    summary_incident = get_summary("gateway", window_seconds=time.time() - t2_start + 1.0)
    print("  -> " + format_phase_summary("INCIDENT", traffic_incident, summary_incident))

    # Take Snapshot during incident for future AI evidence
    incident_snapshot = create_incident_snapshot(env, window_seconds=60.0)
    snapshot_path = incident_snapshot.get("snapshot_file", "in-memory")
    print(f"  [Evidence] Incident snapshot saved: {snapshot_path}")

    # Phase 3: Recovery
    print("\n[Phase 3: Recovery] Resetting payment-service fault state...")
    with httpx.Client(timeout=3.0) as client:
        reset_resp = client.post(f"{PAYMENT_SERVICE_URL}/fault/reset")
        assert reset_resp.status_code == 200

    # Wait briefly for upstream queues to clear
    time.sleep(0.5)

    print(f"  Generating {reqs_per_phase} checkout requests post-recovery...")
    t3_start = time.time()
    traffic_recovery = generate_traffic(num_requests=reqs_per_phase, concurrency=concurrency, interval_seconds=0.02)
    summary_recovery = get_summary("gateway", window_seconds=time.time() - t3_start + 1.0)
    print("  -> " + format_phase_summary("RECOVERY", traffic_recovery, summary_recovery))

    # Summary Table
    print("\n" + "-" * 90)
    print(f"{'PHASE':<12} | {'TOTAL':<5} | {'SUCCESS':<7} | {'FAILED':<6} | {'ERR RATE':<8} | {'AVG LAT':<9} | {'P95 LAT':<9} | {'STATUSES'}")
    print("-" * 90)
    for name, tr in [("Baseline", traffic_baseline), ("Incident", traffic_incident), ("Recovery", traffic_recovery)]:
        print(
            f"{name:<12} | {tr['total_requests']:<5} | {tr['successful_requests']:<7} | "
            f"{tr['failed_requests']:<6} | {tr['error_rate']*100:>7.1f}% | "
            f"{tr['avg_latency_ms']:>7.1f}ms | {tr['p95_latency_ms']:>7.1f}ms | {tr['status_distribution']}"
        )
    print("=" * 90 + "\n")

    return {
        "baseline": {"traffic": traffic_baseline, "summary": summary_baseline},
        "incident": {"traffic": traffic_incident, "summary": summary_incident},
        "recovery": {"traffic": traffic_recovery, "summary": summary_recovery},
        "snapshot_path": snapshot_path
    }
