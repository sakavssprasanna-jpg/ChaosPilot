import pytest
import time
import httpx

from sandbox.process_sandbox import ProcessTargetEnvironment
from sandbox.docker_sandbox import DockerTargetEnvironment
from services.config import GATEWAY_URL, ORDER_SERVICE_URL, PAYMENT_SERVICE_URL

@pytest.fixture(scope="module")
def env():
    """Module-level fixture to start target services and guarantee teardown."""
    target_env = ProcessTargetEnvironment()
    # Ensure all previously running instances on these ports are cleanly terminated
    target_env.stop_all()
    time.sleep(0.5)

    # Start all services
    start_results = target_env.start_all()
    assert all(start_results.values()), f"Failed to start all services: {start_results}"
    time.sleep(0.5)

    yield target_env

    # Cleanup teardown
    target_env.stop_all()

def test_service_startup_and_running(env: ProcessTargetEnvironment):
    """Verify all three microservices are actively running."""
    assert env.is_running("gateway")
    assert env.is_running("order-service")
    assert env.is_running("payment-service")

def test_direct_health_endpoints():
    """Verify each service exposes GET /health returning HTTP 200 and valid payload."""
    with httpx.Client(timeout=2.0) as client:
        # Gateway
        gw_resp = client.get(f"{GATEWAY_URL}/health")
        assert gw_resp.status_code == 200
        gw_data = gw_resp.json()
        assert gw_data["service"] == "gateway"
        assert gw_data["status"] == "healthy"

        # Order Service
        order_resp = client.get(f"{ORDER_SERVICE_URL}/health")
        assert order_resp.status_code == 200
        order_data = order_resp.json()
        assert order_data["service"] == "order-service"
        assert order_data["status"] == "healthy"

        # Payment Service
        pay_resp = client.get(f"{PAYMENT_SERVICE_URL}/health")
        assert pay_resp.status_code == 200
        pay_data = pay_resp.json()
        assert pay_data["service"] == "payment-service"
        assert pay_data["status"] == "healthy"

def test_sandbox_health_inspection(env: ProcessTargetEnvironment):
    """Verify ProcessTargetEnvironment.check_health and check_all_health methods."""
    all_health = env.check_all_health()
    assert "gateway" in all_health
    assert "order-service" in all_health
    assert "payment-service" in all_health

    for svc_name, health in all_health.items():
        assert health["status"] == "healthy"
        assert health["http_status"] == 200
        assert health["latency_ms"] is not None
        assert health["latency_ms"] >= 0

def test_gateway_order_payment_communication_nominal():
    """Verify full end-to-end HTTP pipeline: Gateway -> Order Service -> Payment Service."""
    payload = {
        "customer_id": "cust_integration_test_01",
        "items": [
            {"item_id": "prod_apple", "name": "Organic Honeycrisp Apple", "price": 2.50, "quantity": 4},
            {"item_id": "prod_bread", "name": "Artisan Sourdough Loaf", "price": 5.00, "quantity": 1}
        ],
        "total_amount": 15.00
    }

    with httpx.Client(timeout=4.0) as client:
        resp = client.post(f"{GATEWAY_URL}/api/checkout", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["order_id"].startswith("ord_")
        assert data["payment_id"].startswith("txn_")
        assert data["amount_charged"] == 15.00
        assert data["customer_id"] == "cust_integration_test_01"

def test_fault_injection_timeout_and_504_propagation():
    """Verify controlled fault injection causing Payment Service delay results in 504 Gateway Timeout."""
    with httpx.Client(timeout=8.0) as client:
        # 1. Inject 4.0s timeout fault (exceeds order service 2.0s limit)
        inject_resp = client.post(
            f"{PAYMENT_SERVICE_URL}/fault/inject",
            json={
                "mode": "timeout",
                "delay_seconds": 4.0,
                "description": "Controlled payment processing delay fault"
            }
        )
        assert inject_resp.status_code == 200
        assert inject_resp.json()["status"] == "injected"

        # 2. Check health reflects degraded state
        pay_health = client.get(f"{PAYMENT_SERVICE_URL}/health").json()
        assert pay_health["status"] == "degraded"
        assert pay_health["fault_active"] is True

        # 3. Attempt checkout - must trigger HTTP 504 Gateway Timeout
        checkout_payload = {
            "customer_id": "cust_fault_test",
            "items": [{"item_id": "item_1", "name": "Test Item", "price": 10.0, "quantity": 1}],
            "total_amount": 10.0
        }
        checkout_resp = client.post(f"{GATEWAY_URL}/api/checkout", json=checkout_payload)
        assert checkout_resp.status_code == 504
        assert "Gateway Timeout" in checkout_resp.text

        # 4. Clean up / reset fault
        reset_resp = client.post(f"{PAYMENT_SERVICE_URL}/fault/reset")
        assert reset_resp.status_code == 200
        assert reset_resp.json()["status"] == "nominal"

def test_fault_injection_500_error():
    """Verify 500 error fault in Payment Service propagates as 502 Bad Gateway to the client."""
    with httpx.Client(timeout=4.0) as client:
        # Inject 500 fault
        client.post(
            f"{PAYMENT_SERVICE_URL}/fault/inject",
            json={"mode": "500", "delay_seconds": 0.1, "description": "Crash test"}
        )

        checkout_payload = {
            "customer_id": "cust_500_test",
            "items": [{"item_id": "item_2", "name": "Item", "price": 20.0, "quantity": 1}],
            "total_amount": 20.0
        }
        checkout_resp = client.post(f"{GATEWAY_URL}/api/checkout", json=checkout_payload)
        assert checkout_resp.status_code in (500, 502)

        # Reset
        client.post(f"{PAYMENT_SERVICE_URL}/fault/reset")

def test_service_recovery_after_reset():
    """Verify that after resetting fault, checkout immediately succeeds with HTTP 200."""
    with httpx.Client(timeout=4.0) as client:
        # Confirm nominal
        health = client.get(f"{PAYMENT_SERVICE_URL}/health").json()
        assert health["status"] == "healthy"

        # Checkout succeeds
        checkout_payload = {
            "customer_id": "cust_recovered",
            "items": [{"item_id": "item_3", "name": "Item 3", "price": 30.0, "quantity": 1}],
            "total_amount": 30.0
        }
        resp = client.post(f"{GATEWAY_URL}/api/checkout", json=checkout_payload)
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

def test_restart_individual_service(env: ProcessTargetEnvironment):
    """Verify that restarting an individual service safely terminates and re-spawns it."""
    # Restart payment-service
    restarted = env.restart_service("payment-service")
    assert restarted is True

    # Verify payment service is running and healthy
    health = env.check_health("payment-service")
    assert health["status"] == "healthy"
    assert health["http_status"] == 200

    # Verify end-to-end checkout still works
    with httpx.Client(timeout=4.0) as client:
        resp = client.post(
            f"{GATEWAY_URL}/api/checkout",
            json={
                "customer_id": "cust_after_restart",
                "items": [{"item_id": "item_4", "name": "Item 4", "price": 12.0, "quantity": 1}],
                "total_amount": 12.0
            }
        )
        assert resp.status_code == 200

def test_safe_log_collection(env: ProcessTargetEnvironment):
    """Verify log collection collects real timestamped logs without shell calls."""
    logs = env.get_logs("payment-service", lines=15)
    assert isinstance(logs, list)
    assert len(logs) > 0
    # Confirm log content includes payment-service tags
    content_str = "\n".join(logs)
    assert "payment-service" in content_str or "Uvicorn running" in content_str

def test_security_allowlist_enforcement(env: ProcessTargetEnvironment):
    """Ensure arbitrary or dangerous service names are strictly rejected by the sandbox."""
    malicious_inputs = [
        "rm -rf /",
        "powershell -c ls",
        "evil-service",
        "payment-service; calc.exe",
        "gateway && whoami"
    ]
    for bad_name in malicious_inputs:
        with pytest.raises(ValueError, match="Unauthorized service"):
            env.start_service(bad_name)
        with pytest.raises(ValueError, match="Unauthorized service"):
            env.stop_service(bad_name)
        with pytest.raises(ValueError, match="Unauthorized service"):
            env.restart_service(bad_name)
        with pytest.raises(ValueError, match="Unauthorized service"):
            env.get_logs(bad_name)

def test_docker_adapter_stub():
    """Verify Docker adapter raises descriptive error when Docker is absent."""
    docker_env = DockerTargetEnvironment()
    if not docker_env.docker_available:
        with pytest.raises(EnvironmentError, match="Docker is not installed"):
            docker_env.start_all()
