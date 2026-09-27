import asyncio
import time
from typing import Any, Dict, List
import httpx
from services.config import GATEWAY_URL
from telemetry.db import calculate_percentile

async def _send_single_request(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    index: int,
    interval_seconds: float
) -> Dict[str, Any]:
    if interval_seconds > 0 and index > 0:
        await asyncio.sleep(interval_seconds)

    async with sem:
        payload = {
            "customer_id": f"cust_traffic_{index:03d}",
            "items": [
                {"item_id": f"item_{index % 5}", "name": f"Product-{index % 5}", "price": 19.99, "quantity": 1}
            ],
            "total_amount": 19.99
        }

        t0 = time.time()
        try:
            resp = await client.post(f"{GATEWAY_URL}/api/checkout", json=payload)
            latency_ms = round((time.time() - t0) * 1000, 2)
            return {
                "index": index,
                "status_code": resp.status_code,
                "latency_ms": latency_ms,
                "is_error": resp.status_code >= 400,
                "is_timeout": resp.status_code == 504,
                "error": resp.text if resp.status_code >= 400 else None
            }
        except httpx.TimeoutException:
            latency_ms = round((time.time() - t0) * 1000, 2)
            return {
                "index": index,
                "status_code": 504,
                "latency_ms": latency_ms,
                "is_error": True,
                "is_timeout": True,
                "error": "Client timeout waiting for Gateway"
            }
        except Exception as exc:
            latency_ms = round((time.time() - t0) * 1000, 2)
            return {
                "index": index,
                "status_code": 503,
                "latency_ms": latency_ms,
                "is_error": True,
                "is_timeout": False,
                "error": str(exc)
            }

async def _generate_traffic_async(
    num_requests: int,
    concurrency: int,
    interval_seconds: float,
    timeout_per_request: float
) -> Dict[str, Any]:
    # Strictly bounded limits
    bounded_num = max(1, min(num_requests, 500))
    bounded_concurrency = max(1, min(concurrency, 20))

    sem = asyncio.Semaphore(bounded_concurrency)
    start_time = time.time()

    async with httpx.AsyncClient(timeout=timeout_per_request) as client:
        tasks = [
            _send_single_request(client, sem, i, interval_seconds)
            for i in range(bounded_num)
        ]
        results = await asyncio.gather(*tasks)

    duration = round(time.time() - start_time, 2)
    latencies = [r["latency_ms"] for r in results]
    successes = sum(1 for r in results if not r["is_error"])
    failures = sum(1 for r in results if r["is_error"])
    timeouts = sum(1 for r in results if r["is_timeout"])

    status_counts: Dict[int, int] = {}
    for r in results:
        code = r["status_code"]
        status_counts[code] = status_counts.get(code, 0) + 1

    avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
    p95_latency = calculate_percentile(latencies, 95)
    p99_latency = calculate_percentile(latencies, 99)

    return {
        "total_requests": bounded_num,
        "concurrency": bounded_concurrency,
        "duration_seconds": duration,
        "successful_requests": successes,
        "failed_requests": failures,
        "timeout_requests": timeouts,
        "error_rate": round(failures / bounded_num, 4) if bounded_num > 0 else 0.0,
        "avg_latency_ms": avg_latency,
        "p95_latency_ms": p95_latency,
        "p99_latency_ms": p99_latency,
        "status_distribution": status_counts,
        "results": results
    }

def generate_traffic(
    num_requests: int = 15,
    concurrency: int = 2,
    interval_seconds: float = 0.02,
    timeout_per_request: float = 6.0
) -> Dict[str, Any]:
    """Synchronous entry point for generating bounded traffic."""
    return asyncio.run(
        _generate_traffic_async(num_requests, concurrency, interval_seconds, timeout_per_request)
    )
