# Runbook: API Gateway 504 Timeout and Upstream Circuit Breakdown

## Overview
This runbook covers operational troubleshooting when the API Gateway (port 8000) produces HTTP 504 Gateway Timeout responses to client requests.

## Architecture & Dependencies
- Edge Gateway: `gateway` (Port 8000)
- Downstream Target: `order-service` (Port 8001)
- Deep Downstream Dependency: `payment-service` (Port 8002)
- Telemetry Store: Local SQLite database at `logs/telemetry.db`

## Timeout Budget & SLA
- Gateway client timeout to Order Service: 3.0 seconds (`GATEWAY_TO_ORDER_TIMEOUT`).
- Under nominal traffic, the full chain Gateway -> Order -> Payment completes in under 50ms.
- An HTTP 504 indicates that an intermediate or downstream service exceeded its allocated response budget.

## Failure Signature & Symptoms
- Client responses from `POST /api/checkout` return status code 504.
- Gateway logs show: `[ERROR] [gateway] Checkout timed out waiting for Order Service (> 3.0s)`.
- Gateway `/metrics` shows a sharp rise in `timeout_count` and `error_rate`.
- Gateway `/api/system/status` shows overall status `"degraded"` or `"down"`.

## Root Cause Diagnosis
1. Query system-wide aggregated status via `GET http://127.0.0.1:8000/api/system/status`.
2. Differentiate between Order Service failure vs Payment Service failure:
   - If `order_service` reports healthy but slow, check if Order Service is waiting on `payment_service`.
   - In `logs/order-service.log`, look for `Payment service timeout (> 2.0s) while processing order`.
3. If `payment-service` is degraded, refer to `knowledge/runbooks/payment_timeout.md`.
4. Inspect recent code changes in `services/config.py` for inadvertent reductions in timeout settings or port misconfigurations.

## Safe Remediation Procedure
1. Identify the blocking downstream service using `python manage.py status`.
2. If `order-service` is hung:
   Execute allowlisted restart: `python manage.py restart order-service`.
3. If `payment-service` is hung or degraded:
   Execute allowlisted restart: `python manage.py restart payment-service`.
4. If Gateway itself is unresponsive:
   Execute allowlisted restart: `python manage.py restart gateway`.

## Verification Procedure
1. Query `python manage.py status` to ensure all 3 services report `"status": "healthy"`.
2. Send test checkout: `python manage.py checkout`.
3. Check telemetry with `python manage.py metrics --service gateway` to ensure new requests record HTTP 200.

## Escalation Policy
- Never modify system networking, firewall, or host loopback adapters.
- Use only the allowlisted sandbox supervisor for process manipulation.
