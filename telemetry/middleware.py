import time
from typing import Dict, List
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from telemetry.db import record_metric, calculate_percentile

class ServiceTelemetry:
    """In-memory telemetry accumulator for instant service-level inspection."""
    def __init__(self, service_name: str):
        self.service_name = service_name
        self.request_count = 0
        self.success_count = 0
        self.error_count = 0
        self.timeout_count = 0
        self.latencies: List[float] = []
        self.status_distribution: Dict[int, int] = {}

    def record(self, status_code: int, latency_ms: float, is_error: bool, is_timeout: bool):
        self.request_count += 1
        self.status_distribution[status_code] = self.status_distribution.get(status_code, 0) + 1
        self.latencies.append(latency_ms)
        # Keep sliding memory bounded to last 1000 requests
        if len(self.latencies) > 1000:
            self.latencies.pop(0)

        if is_error:
            self.error_count += 1
        else:
            self.success_count += 1

        if is_timeout:
            self.timeout_count += 1

    def get_summary(self) -> dict:
        total = self.request_count
        avg_lat = round(sum(self.latencies) / len(self.latencies), 2) if self.latencies else 0.0
        p95 = calculate_percentile(self.latencies, 95)
        p99 = calculate_percentile(self.latencies, 99)
        return {
            "service": self.service_name,
            "request_count": total,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "error_rate": round(self.error_count / total, 4) if total > 0 else 0.0,
            "avg_latency_ms": avg_lat,
            "p95_latency_ms": p95,
            "p99_latency_ms": p99,
            "timeout_count": self.timeout_count,
            "status_distribution": dict(self.status_distribution)
        }

class TelemetryMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware to intercept all requests, measure real latency, and record telemetry."""
    def __init__(self, app, service_name: str):
        super().__init__(app)
        self.service_name = service_name
        self.telemetry = ServiceTelemetry(service_name)

    async def dispatch(self, request: Request, call_next):
        # Ignore internal metrics/health polling from skewing main business request telemetry if desired,
        # but record all API endpoints. Let's record all endpoints including /health so observability is 100% real.
        t0 = time.time()
        status_code = 500
        is_timeout = False
        error_detail = None

        try:
            response = await call_next(request)
            status_code = response.status_code
            latency_ms = round((time.time() - t0) * 1000, 2)
            is_error = status_code >= 400
            is_timeout = status_code == 504

            # Record in-memory
            self.telemetry.record(status_code, latency_ms, is_error, is_timeout)

            # Persist to database
            try:
                record_metric(
                    service=self.service_name,
                    endpoint=request.url.path,
                    method=request.method,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    is_error=is_error,
                    is_timeout=is_timeout,
                    error_detail=error_detail,
                    timestamp=t0
                )
            except Exception:
                pass

            return response

        except Exception as exc:
            latency_ms = round((time.time() - t0) * 1000, 2)
            status_code = 500
            error_detail = str(exc)
            self.telemetry.record(status_code, latency_ms, is_error=True, is_timeout=False)
            try:
                record_metric(
                    service=self.service_name,
                    endpoint=request.url.path,
                    method=request.method,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    is_error=True,
                    is_timeout=False,
                    error_detail=error_detail,
                    timestamp=t0
                )
            except Exception:
                pass
            raise
