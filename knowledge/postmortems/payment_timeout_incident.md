# Postmortem: Incident INC-2026-0927 Payment Gateway Cascading Timeout

## Overview
- Date: 2026-09-27
- Impacted Services: `gateway` (Port 8000), `order-service` (Port 8001), `payment-service` (Port 8002)
- Incident Duration: 12 minutes
- User Impact: 100% of checkout requests failed with HTTP 504 Gateway Timeout during the fault window.

## Incident Timeline
- 16:50 UTC - Initial alert triggered: Gateway error rate spiked from 0.0% to 100%. P95 latency jumped from 42ms to 3,680ms.
- 16:52 UTC - On-call engineer inspected `GET http://127.0.0.1:8000/api/system/status` and identified `payment-service` in a degraded state.
- 16:54 UTC - Evidence review of `logs/payment-service.log` revealed `[FAULT-ACTIVE] Introducing artificial delay of 4.0s for order`.
- 16:56 UTC - Root cause identified: Downstream payment processing delay exceeded the 2.0s upstream client timeout in `order-service`.
- 16:58 UTC - Remediation action executed: `POST http://127.0.0.1:8002/fault/reset` followed by automated verification.
- 17:02 UTC - Verification confirmed: Checkout latency returned to 35ms, error rate dropped to 0.0%, incident resolved.

## Root Cause Analysis
1. Actual Implemented Behavior:
   Payment Service was configured with a controlled fault state introducing an artificial 4.0-second delay on `POST /payments/process`.
2. Controlled Fault Mechanism:
   The upstream orchestrator, `order-service`, enforces an explicit 2.0-second client timeout (`ORDER_TO_PAYMENT_TIMEOUT`). When payment processing exceeded 2.0 seconds, `order-service` terminated the connection with an `httpx.TimeoutException` and returned HTTP 504. The API Gateway then relayed an HTTP 504 Gateway Timeout to the client.
3. Hypothetical Production Interpretation:
   In a production environment, such downstream latencies typically correspond to database lock contention, thread exhaustion, or external payment processor outages. In this controlled local sandbox, the outage is produced deterministically via the explicit payment timeout fault injector.

## Remediation & Recovery Actions
1. Immediate Mitigation:
   Issue in-place reset (`POST /fault/reset`) or execute process restart via sandbox supervisor:
   `python manage.py restart payment-service`.
2. Verification:
   Confirm health check on port 8002 returns HTTP 200 `"status": "healthy"`.
   Run verification traffic to confirm Gateway latency drops back below 50ms and error rate reaches 0.0%.

## Lessons Learned & Action Items
- Runbook RB-101 (`knowledge/runbooks/payment_timeout.md`) must be consulted immediately when Gateway 504s are observed.
- Telemetry snapshots should be captured at the onset of degradation to preserve evidence logs and error rate histograms for post-incident review.
