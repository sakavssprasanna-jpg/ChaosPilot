import argparse
import json
import sys
import time
import httpx
import io

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from sandbox.process_sandbox import ProcessTargetEnvironment
from services.config import GATEWAY_URL, PAYMENT_SERVICE_URL

def cmd_start(env: ProcessTargetEnvironment):
    print("[ChaosPilot] Starting all microservices (Payment -> Order -> Gateway)...")
    res = env.start_all()
    for svc, started in res.items():
        print(f"  * {svc}: {'STARTED' if started else 'FAILED'}")
    print("\nCurrent Status:")
    cmd_status(env)

def cmd_stop(env: ProcessTargetEnvironment):
    print("[ChaosPilot] Stopping all microservices...")
    res = env.stop_all()
    for svc, stopped in res.items():
        print(f"  * {svc}: {'STOPPED' if stopped else 'ERROR'}")

def cmd_restart(env: ProcessTargetEnvironment, service_name: str):
    print(f"[ChaosPilot] Restarting {service_name}...")
    success = env.restart_service(service_name)
    print(f"  * {service_name}: {'RESTARTED & HEALTHY' if success else 'RESTART FAILED'}")

def cmd_status(env: ProcessTargetEnvironment):
    health = env.check_all_health()
    print(json.dumps(health, indent=2))

def cmd_logs(env: ProcessTargetEnvironment, service_name: str, lines: int):
    log_lines = env.get_logs(service_name, lines=lines)
    print(f"\n--- Logs for {service_name} (Last {len(log_lines)} lines) ---")
    for l in log_lines:
        print(l)

def cmd_checkout():
    print(f"[ChaosPilot] Sending checkout request to Gateway ({GATEWAY_URL}/api/checkout)...")
    payload = {
        "customer_id": "cust_demo_tester",
        "items": [
            {"item_id": "item_401", "name": "Cloud Storage Tier 1", "price": 49.99, "quantity": 1}
        ],
        "total_amount": 49.99
    }
    t0 = time.time()
    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.post(f"{GATEWAY_URL}/api/checkout", json=payload)
            latency_ms = round((time.time() - t0) * 1000, 2)
            print(f"HTTP Status: {resp.status_code} ({latency_ms}ms)")
            print(f"Response: {resp.text}")
    except Exception as e:
        latency_ms = round((time.time() - t0) * 1000, 2)
        print(f"Request failed ({latency_ms}ms): {e}")

def cmd_fault_inject(mode: str, delay: float):
    print(f"[ChaosPilot] Injecting fault into Payment Service (mode={mode}, delay={delay}s)...")
    payload = {
        "mode": mode,
        "delay_seconds": delay,
        "error_code": 504,
        "description": "Controlled payment-processing timeout fault (artificial delay exceeding upstream budget)"
    }
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.post(f"{PAYMENT_SERVICE_URL}/fault/inject", json=payload)
            print(f"HTTP {resp.status_code}: {resp.json()}")
    except Exception as e:
        print(f"Failed to inject fault: {e}")

def cmd_fault_reset():
    print(f"[ChaosPilot] Resetting fault on Payment Service...")
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.post(f"{PAYMENT_SERVICE_URL}/fault/reset")
            print(f"HTTP {resp.status_code}: {resp.json()}")
    except Exception as e:
        print(f"Failed to reset fault: {e}")

def cmd_run(env: ProcessTargetEnvironment):
    print("[ChaosPilot] Starting all microservices in supervisor mode...")
    res = env.start_all()
    for svc, started in res.items():
        print(f"  * {svc}: {'STARTED' if started else 'FAILED'}")
    
    print("\nAll microservices active. Press Ctrl+C to stop all services.")
    try:
        while True:
            time.sleep(2.0)
    except KeyboardInterrupt:
        print("\nShutdown signal received. Stopping all services...")
        env.stop_all()
        print("All microservices cleanly stopped.")

def cmd_metrics(service: str = None):
    from telemetry.collector import TelemetryCollector
    collector = TelemetryCollector()
    if service:
        data = collector.get_service_summary(service)
    else:
        data = collector.get_all_summaries()
    print(json.dumps(data, indent=2))

def cmd_traffic(count: int, concurrency: int, interval: float):
    from telemetry.traffic import generate_traffic
    print(f"[ChaosPilot] Generating {count} checkout requests (concurrency={concurrency}, interval={interval}s)...")
    res = generate_traffic(num_requests=count, concurrency=concurrency, interval_seconds=interval)
    print(f"Completed in {res['duration_seconds']}s:")
    print(f"  * Total: {res['total_requests']}")
    print(f"  * Success: {res['successful_requests']}")
    print(f"  * Failed: {res['failed_requests']}")
    print(f"  * Error Rate: {res['error_rate']*100:.1f}%")
    print(f"  * Avg Latency: {res['avg_latency_ms']}ms (P95: {res['p95_latency_ms']}ms)")
    print(f"  * HTTP Statuses: {res['status_distribution']}")

def cmd_snapshot(env: ProcessTargetEnvironment):
    from telemetry.snapshot import create_incident_snapshot
    print("[ChaosPilot] Generating environment incident snapshot...")
    snapshot = create_incident_snapshot(env)
    print(f"Snapshot successfully saved to: {snapshot.get('snapshot_file')}")
    print(f"  * Overall status: {snapshot['overall_status']}")
    print(f"  * Timestamp: {snapshot['timestamp_iso']}")

def cmd_incident(env: ProcessTargetEnvironment, count: int, concurrency: int):
    from telemetry.incident import run_deterministic_incident
    run_deterministic_incident(env, reqs_per_phase=count, concurrency=concurrency)

def cmd_retrieve(query: str, top_k: int = 5):
    from rag.retriever import retrieve
    citations = retrieve(query, top_k=top_k)
    print(f"\n[ChaosPilot RAG] Search Results for query: '{query}' ({len(citations)} chunks found):\n")
    for i, c in enumerate(citations, 1):
        print(f"EVIDENCE {i}")
        print(f"Source:  {c.document}")
        print(f"Section: {c.section}")
        print(f"Score:   {c.score:.4f}")
        print(f"Content:\n{c.text_snippet}\n" + "-" * 70)

def cmd_evidence(snapshot_path: str = None):
    from pathlib import Path
    from services.config import LOGS_DIR
    from rag.coordinator import diagnose_evidence

    # Locate latest snapshot if not specified
    if not snapshot_path:
        snap_dir = LOGS_DIR / "snapshots"
        snapshots = sorted(snap_dir.glob("snapshot_*.json")) if snap_dir.exists() else []
        if snapshots:
            snapshot_path = str(snapshots[-1])
            print(f"[ChaosPilot Evidence] Using latest incident snapshot: {snapshot_path}")
        else:
            print("[ChaosPilot Evidence] No snapshot found. Please run 'python manage.py snapshot' or 'python manage.py incident' first.")
            return

    bundle = diagnose_evidence(snapshot_path)
    print(f"\n======================================================================")
    print(f" [ChaosPilot] EVIDENCE BUNDLE: {bundle.query}")
    print(f"======================================================================\n")

    print("DOCUMENT EVIDENCE:")
    for i, c in enumerate(bundle.documents, 1):
        print(f"EVIDENCE {i}")
        print(f"Source:  {c.document}")
        print(f"Section: {c.section}")
        print(f"Score:   {c.score:.4f}")
        print(f"Content:\n{c.text_snippet}\n" + "-" * 70)

    print("\nGIT EVIDENCE:")
    if bundle.git_evidence:
        for i, g in enumerate(bundle.git_evidence, 1):
            print(f"GIT COMMIT {i}: [{g.commit}] (Relevance: {g.relevance:.3f})")
            print(f"Author:  {g.author}")
            print(f"Date:    {g.date}")
            print(f"Message: {g.message}")
            print(f"Files:   {', '.join(g.files_changed) if g.files_changed else 'None'}")
            if g.diff:
                print(f"Diff Patch:\n{g.diff[:400]}...")
            print("-" * 70)
    else:
        print("No matching git commits found for diagnostic terms.")

    print(f"\nCITED SOURCES: {', '.join(bundle.sources)}\n")

def main():
    parser = argparse.ArgumentParser(description="ChaosPilot Management CLI (Stage 1 + Stage 2 + Stage 3)")
    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    subparsers.add_parser("start", help="Start all microservices")
    subparsers.add_parser("run", help="Run all microservices in foreground supervisor mode")
    subparsers.add_parser("stop", help="Stop all microservices")
    subparsers.add_parser("status", help="Check health of all microservices")
    subparsers.add_parser("checkout", help="Send test checkout request through Gateway")

    # Metrics
    metrics_p = subparsers.add_parser("metrics", help="Query real calculated telemetry metrics")
    metrics_p.add_argument("--service", choices=["gateway", "order-service", "payment-service"], default=None)

    # Traffic
    traffic_p = subparsers.add_parser("traffic", help="Generate bounded checkout traffic")
    traffic_p.add_argument("--count", type=int, default=10, help="Total requests (max 500)")
    traffic_p.add_argument("--concurrency", type=int, default=2, help="Concurrency (max 20)")
    traffic_p.add_argument("--interval", type=float, default=0.02, help="Interval between requests (seconds)")

    # Snapshot
    subparsers.add_parser("snapshot", help="Create structured incident snapshot for SRE evidence")

    # Incident
    incident_p = subparsers.add_parser("incident", help="Run deterministic 3-phase SRE incident simulation")
    incident_p.add_argument("--count", type=int, default=10, help="Requests per phase")
    incident_p.add_argument("--concurrency", type=int, default=2, help="Concurrency per phase")

    # Retrieve (RAG)
    retrieve_p = subparsers.add_parser("retrieve", help="Search knowledge base for diagnostic evidence")
    retrieve_p.add_argument("query", type=str, help="Search query")
    retrieve_p.add_argument("--top-k", type=int, default=5, help="Number of chunks to retrieve")

    # Evidence
    evidence_p = subparsers.add_parser("evidence", help="Derive diagnostic evidence from incident snapshot")
    evidence_p.add_argument("--snapshot", type=str, default=None, help="Path to snapshot JSON (defaults to latest)")

    restart_p = subparsers.add_parser("restart", help="Restart a specific service")
    restart_p.add_argument("service", choices=["gateway", "order-service", "payment-service"])

    logs_p = subparsers.add_parser("logs", help="Get logs of a specific service")
    logs_p.add_argument("service", choices=["gateway", "order-service", "payment-service"])
    logs_p.add_argument("--lines", type=int, default=30, help="Number of lines to tail")

    fault_p = subparsers.add_parser("fault", help="Fault injection control")
    fault_sub = fault_p.add_subparsers(dest="fault_action")
    inject_p = fault_sub.add_parser("inject")
    inject_p.add_argument("--mode", default="timeout", choices=["timeout", "500", "503"])
    inject_p.add_argument("--delay", type=float, default=5.0)
    fault_sub.add_parser("reset")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    env = ProcessTargetEnvironment()

    if args.command == "start":
        cmd_start(env)
    elif args.command == "run":
        cmd_run(env)
    elif args.command == "stop":
        cmd_stop(env)
    elif args.command == "restart":
        cmd_restart(env, args.service)
    elif args.command == "status":
        cmd_status(env)
    elif args.command == "logs":
        cmd_logs(env, args.service, args.lines)
    elif args.command == "checkout":
        cmd_checkout()
    elif args.command == "metrics":
        cmd_metrics(args.service)
    elif args.command == "traffic":
        cmd_traffic(args.count, args.concurrency, args.interval)
    elif args.command == "snapshot":
        cmd_snapshot(env)
    elif args.command == "incident":
        cmd_incident(env, args.count, args.concurrency)
    elif args.command == "retrieve":
        cmd_retrieve(args.query, top_k=args.top_k)
    elif args.command == "evidence":
        cmd_evidence(args.snapshot)
    elif args.command == "fault":
        if args.fault_action == "inject":
            cmd_fault_inject(args.mode, args.delay)
        elif args.fault_action == "reset":
            cmd_fault_reset()
        else:
            fault_p.print_help()

if __name__ == "__main__":
    main()
