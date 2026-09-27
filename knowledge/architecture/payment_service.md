# Architecture: Payment Service Specifications and Fault Injection

## Overview
The Payment Service (`payment-service`) is the core downstream dependency in the ChaosPilot topology, running as a FastAPI process on localhost port 8002.

## Endpoints & Contract
- `GET /health`:
  - Returns service identification, version ("1.0.4"), current health status ("healthy" or "degraded"), and fault status flags.
- `POST /payments/process`:
  - Consumes `PaymentRequest` (order_id, amount, currency, customer_id).
  - Produces `PaymentResponse` (transaction_id, status="success", timestamp).
  - Nominal latency is 20ms to 40ms.
- `GET /metrics`:
  - Returns structured telemetry summary for Payment Service.
- `POST /fault/inject`:
  - Injects simulated operational faults into the payment request pipeline.
- `POST /fault/reset`:
  - Clears active fault states and restores nominal sub-50ms execution.
- `GET /fault/status`:
  - Exposes current fault configuration (mode, delay_seconds, description, injected_at).

## Fault Simulation Mechanics
The service supports three controlled failure modes:
1. `timeout`:
   - Introduces a controlled artificial processing delay into the payment transaction pipeline.
   - Introduces an artificial asynchronous sleep (`delay_seconds`, default 4.0s).
   - Because upstream `order-service` has an `ORDER_TO_PAYMENT_TIMEOUT` of 2.0 seconds, this causes the upstream client to terminate the connection and produce an HTTP 504.
2. `500`:
   - Immediately raises an internal server error simulating severe unhandled exceptions.
3. `503`:
   - Simulates downstream circuit-breaker opening or backend unavailability.

## Logging & Tracing
- Log destination: `logs/payment-service.log`.
- Log entries include timestamp, log level, service tag (`[payment-service]`), and operation markers (`[FAULT-ACTIVE]`, `Payment successful`).
