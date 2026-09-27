# Architecture: Microservices Topology and Network Flow

## Overview
ChaosPilot operates a local 3-tier microservice architecture designed for deterministic incident simulation and autonomous recovery within a controlled Python process sandbox.

## Architecture & Dependencies
The service topology consists of three dedicated FastAPI processes communicating over localhost TCP ports:
1. `gateway` (Port 8000):
   - Edge entrypoint for all external client traffic.
   - Routes `/api/checkout` to the Order Service.
   - Aggregates health status via `/api/system/status`.
2. `order-service` (Port 8001):
   - Business logic coordinator for customer checkout transactions.
   - Manages in-memory order persistence.
   - Dispatches payment processing requests to Payment Service.
3. `payment-service` (Port 8002):
   - Core financial transaction processing engine.
   - Implements configurable fault simulation modes (timeout, 500, 503).
   - Generates unique transaction identifiers for confirmed payments.

## Communication Protocol & Timeout Configuration
- Transport: HTTP/1.1 JSON REST APIs.
- Client Libraries: Asynchronous `httpx.AsyncClient` with non-blocking I/O.
- Timeout Thresholds:
  - Gateway -> Order Service: 3.0 seconds (`GATEWAY_TO_ORDER_TIMEOUT`).
  - Order Service -> Payment Service: 2.0 seconds (`ORDER_TO_PAYMENT_TIMEOUT`).
- Because Order Service times out at 2.0s while Gateway waits 3.0s, downstream Payment delays between 2.0s and 3.0s trigger Order Service timeouts that propagate to Gateway as 504 Gateway Timeouts.

## Telemetry & Observability Subsystem
- Ingress Middleware: Every service wraps incoming requests with `TelemetryMiddleware`.
- Storage: High-performance SQLite database in WAL mode located at `logs/telemetry.db`.
- Metric Metrics: Request counts, error rates, status code histograms, average latency, P95, and P99 latency percentiles.
- Health Probing: Dedicated `GET /health` endpoints providing sub-millisecond status polling.

## Safe Sandbox Supervision
- Supervisor: `ProcessTargetEnvironment` managing child processes using `psutil`.
- Isolation: All autonomous and manual commands must strictly address allowlisted service names (`gateway`, `order-service`, `payment-service`).
- Zero Shell: No shell commands (`os.system`, `eval`, `exec`) are permitted.
