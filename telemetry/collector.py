from typing import Any, Dict, List, Optional
import httpx
from services.config import SERVICE_MAP
from telemetry.db import get_summary, get_all_summaries

class TelemetryCollector:
    """Collector querying real telemetry data from local SQLite database or directly via service endpoints."""

    def __init__(self, timeout: float = 2.0):
        self.timeout = timeout

    def get_service_summary(self, service_name: str, window_seconds: Optional[float] = None) -> Dict[str, Any]:
        """Calculates structured summary from stored telemetry events for a specific service."""
        return get_summary(service_name, window_seconds=window_seconds)

    def get_all_summaries(self, window_seconds: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
        """Calculates structured summaries across all services."""
        return get_all_summaries(window_seconds=window_seconds)

    def fetch_live_service_metrics(self, service_name: str) -> Dict[str, Any]:
        """Queries the live /metrics endpoint on the running service."""
        if service_name not in SERVICE_MAP:
            raise ValueError(f"Unknown service: {service_name}")

        url = f"{SERVICE_MAP[service_name]['url']}/metrics"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return resp.json()
                return {"service": service_name, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"service": service_name, "error": str(e)}

    def fetch_all_live_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Queries /metrics across all running services."""
        return {svc: self.fetch_live_service_metrics(svc) for svc in SERVICE_MAP}
