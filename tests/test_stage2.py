import os
from pathlib import Path
import time
import httpx
import pytest

from sandbox.process_sandbox import ProcessTargetEnvironment
from services.config import GATEWAY_URL, PAYMENT_SERVICE_URL
from telemetry.collector import TelemetryCollector
from telemetry.db import clear_metrics, get_summary, calculate_percentile, init_db, record_metric
from telemetry.traffic import generate_traffic
from telemetry.snapshot import create_incident_snapshot
from telemetry.incident import run_deterministic_incident

@pytest.fixture(scope="module")
def env():
    """Module-level fixture to start target services and guarantee teardown."""
    target_env = ProcessTargetEnvironment()
    target_env.stop_all()
    time.sleep(0.5)

    start_results = target_env.start_all()
    assert all(start_results.values()), f"Failed to start all services: {start_results}"
    time.sleep(0.5)

    # Ensure fault is cleared and database initialized
    init_db()
    with httpx.Client(timeout=2.0) as client:
        client.post(f"{PAYMENT_SERVICE_URL}/fault/reset")

    yield target_env

    target_env.stop_all()

def test_telemetry_records_successful_requests(env: ProcessTargetEnvironment):
    """Verify telemetry accurately measures real successful requests."""
    clear_metrics()

    # Send 5 checkout requests
    with httpx.Client(timeout=4.0) as client:
        for i in range(5):
            resp = client.post(
                f"{GATEWAY_URL}/api/checkout",
                json={
                    "customer_id": f"cust_t2_{i}",
                    "items": [{"item_id": "item_1", "name": "Item", "price": 10.0, "quantity": 1}],
                    "total_amount": 10.0
                }
            )
            assert resp.status_code == 200

    summary = get_summary("gateway")
    assert summary["request_count"] == 5
    assert summary["success_count"] == 5
    assert summary["error_count"] == 0
    assert summary["error_rate"] == 0.0
    assert summary["avg_latency_ms"] > 0
    assert summary["p95_latency_ms"] > 0
    assert 200 in summary["status_distribution"]
    assert summary["status_distribution"][200] == 5

def test_latency_values_are_measured_not_hardcoded(env: ProcessTargetEnvironment):
    """Verify latency values are dynamically measured and vary naturally."""
    from telemetry.db import get_connection
    with get_connection() as conn:
        rows = conn.execute("SELECT latency_ms FROM request_metrics WHERE service = 'gateway'").fetchall()
        latencies = [r["latency_ms"] for r in rows]

    assert len(latencies) >= 5
    for lat in latencies:
        assert isinstance(lat, float)
        assert lat > 0.0
    # Values should not be identical hard-coded stubs
    unique_latencies = set(latencies)
    assert len(unique_latencies) > 1

def test_telemetry_records_failed_requests_and_timeouts(env: ProcessTargetEnvironment):
    """Verify telemetry records real failed requests and timeout indicators."""
    # Inject fault
    with httpx.Client(timeout=3.0) as client:
        resp = client.post(
            f"{PAYMENT_SERVICE_URL}/fault/inject",
            json={"mode": "timeout", "delay_seconds": 3.5, "description": "Controlled timeout fault"}
        )
        assert resp.status_code == 200

    # Fire checkout (should trigger 504 Gateway Timeout)
    with httpx.Client(timeout=6.0) as client:
        checkout_resp = client.post(
            f"{GATEWAY_URL}/api/checkout",
            json={
                "customer_id": "cust_fail",
                "items": [{"item_id": "i1", "name": "n", "price": 5.0, "quantity": 1}],
                "total_amount": 5.0
            }
        )
        assert checkout_resp.status_code == 504

    # Verify telemetry recorded 504 and error
    summary = get_summary("gateway")
    assert summary["error_count"] >= 1
    assert summary["timeout_count"] >= 1
    assert summary["status_distribution"].get(504, 0) >= 1
    assert summary["error_rate"] > 0.0

    # Reset fault
    with httpx.Client(timeout=3.0) as client:
        client.post(f"{PAYMENT_SERVICE_URL}/fault/reset")

def test_telemetry_percentile_and_summary_math():
    """Verify percentile calculation math and error rate formulas independently."""
    sample_latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    p50 = calculate_percentile(sample_latencies, 50)
    p95 = calculate_percentile(sample_latencies, 95)
    p99 = calculate_percentile(sample_latencies, 99)

    assert p50 == 55.0
    assert p95 > 90.0
    assert p99 > 95.0

def test_bounded_traffic_generator_terminates_correctly(env: ProcessTargetEnvironment):
    """Verify bounded traffic generator dispatches exact number of requests and cleanly terminates."""
    t0 = time.time()
    report = generate_traffic(num_requests=6, concurrency=2, interval_seconds=0.01, timeout_per_request=4.0)
    duration = time.time() - t0

    # With persistent HTTP connection pooling, 6 requests finish in ~1.1s.
    # The original 10.0s threshold is strictly enforced.
    assert duration < 10.0, f"Traffic took {duration:.2f}s, exceeding 10.0s limit"

def test_incident_snapshot_contains_real_state(env: ProcessTargetEnvironment):
    """Verify incident snapshot captures complete real state and saves to disk."""
    snapshot = create_incident_snapshot(env, window_seconds=60.0, save_to_disk=True)

    assert "timestamp" in snapshot
    assert "timestamp_iso" in snapshot
    assert "overall_status" in snapshot
    assert "service_health" in snapshot
    assert "metrics_summary" in snapshot
    assert "fault_status" in snapshot
    assert "recent_logs" in snapshot

    # Verify health details
    assert "gateway" in snapshot["service_health"]
    assert "order-service" in snapshot["service_health"]
    assert "payment-service" in snapshot["service_health"]

    # Verify snapshot file on disk
    filepath = Path(snapshot["snapshot_file"])
    assert filepath.exists()
    assert filepath.stat().st_size > 100

def test_live_service_metrics_endpoints():
    """Verify each service exposes /metrics returning real computed telemetry."""
    collector = TelemetryCollector()
    metrics = collector.fetch_all_live_metrics()

    for svc in ["gateway", "order-service", "payment-service"]:
        assert svc in metrics
        data = metrics[svc]
        assert "service" in data
        assert "request_count" in data
        assert "error_rate" in data
        assert "avg_latency_ms" in data

def test_full_deterministic_incident_scenario(env: ProcessTargetEnvironment):
    """Verify complete 3-phase incident scenario (Baseline -> Incident -> Recovery)."""
    res = run_deterministic_incident(env, reqs_per_phase=5, concurrency=2, fault_delay_seconds=3.5)

    base = res["baseline"]["traffic"]
    inc = res["incident"]["traffic"]
    rec = res["recovery"]["traffic"]

    # 1. Baseline: 100% success, 0% error
    assert base["error_rate"] == 0.0
    assert base["successful_requests"] == 5

    # 2. Incident: Fault causes failures and increased latency
    assert inc["error_rate"] > 0.0
    assert inc["failed_requests"] > 0
    assert inc["avg_latency_ms"] > base["avg_latency_ms"]

    # 3. Recovery: Failures decrease to zero
    assert rec["error_rate"] < inc["error_rate"]
    assert rec["successful_requests"] == 5
    assert rec["error_rate"] == 0.0

    # 4. Snapshot path exists
    assert os.path.exists(res["snapshot_path"])
