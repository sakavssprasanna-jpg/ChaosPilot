# Runbook: Payment Service Timeout and Gateway 504 Mitigation

## Overview
This runbook documents the operational procedure for diagnosing and recovering from downstream timeout incidents originating in the Payment Service (port 8002) that propagate upstream through Order Service (port 8001) to API Gateway (port 8000).

## Architecture & Dependencies
- Upstream Edge: `gateway` (Port 8000)
- Intermediate Orchestrator: `order-service` (Port 8001)
- Downstream Dependency: `payment-service` (Port 8002)
- Flow: Client -> Gateway `/api/checkout` -> Order Service `/orders/create` -> Payment Service `/payments/process`.

## Timeout Budget & SLA
- Order Service to Payment Service Client Timeout: 2.0 seconds (`ORDER_TO_PAYMENT_TIMEOUT`).
- Gateway to Order Service Client Timeout: 3.0 seconds (`GATEWAY_TO_ORDER_TIMEOUT`).
- Nominal Latency: Checkout requests typically complete in 20ms to 45ms.
- Breach Condition: Any payment processing latency exceeding 2.0 seconds triggers an immediate upstream HTTP 504 Gateway Timeout.

## Failure Signature & Symptoms
- API Gateway returns HTTP 504 Gateway Timeout on `/api/checkout`.
- Gateway error detail: `"Gateway Timeout: Order service failed to respond within 3.0s"`.
- Order Service error detail: `"Gateway Timeout: Payment service failed to respond within 2.0s limit."`.
- Payment Service `/health` endpoint reports `"status": "degraded"` with `"fault_active": true` and `"fault_mode": "timeout"`.
- P95 and P99 latency metrics on the Gateway spike to over 3,000ms.
- Gateway error rate increases toward 100% on the checkout endpoint.

## Root Cause Diagnosis
1. Inspect Payment Service health by querying `GET http://127.0.0.1:8002/health`.
2. Inspect Payment Service fault status by querying `GET http://127.0.0.1:8002/fault/status`.
3. Check `logs/payment-service.log` for log markers such as `[FAULT-ACTIVE] Introducing artificial delay` or controlled timeout faults.
4. Check recent Git commits or configuration patches that modified `delay_seconds` or upstream timeout thresholds.

## Safe Remediation Procedure
Execution must follow allowlisted actions:
1. In-Place Fault Clear:
   Send an HTTP POST to `http://127.0.0.1:8002/fault/reset` to immediately clear active fault state.
2. Controlled Process Restart:
   If in-place reset does not restore nominal latency within 5 seconds, perform a safe process restart:
   `python manage.py restart payment-service`
   The process supervisor will terminate the degraded instance and launch a clean instance on port 8002.

## Verification Procedure
1. Health Confirmation:
   Confirm `GET http://127.0.0.1:8002/health` returns HTTP 200 with `"status": "healthy"`.
2. End-to-End Connectivity:
   Execute a single checkout probe via `python manage.py checkout`.
   Verify the response returns HTTP 200 OK with `"status": "success"` and latency < 100ms.
3. Telemetry Normalization:
   Execute bounded verification traffic (`python manage.py traffic --count 5`).
   Confirm that error rate returns to 0.0% and average latency drops below nominal SLA limits.

## Escalation Policy
- If Payment Service fails to respond to health checks after restart, verify port 8002 is not blocked by another process using `manage.py status`.
- Do not execute arbitrary host shell commands or kill non-allowlisted processes.
