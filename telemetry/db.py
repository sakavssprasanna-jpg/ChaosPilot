import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional

from services.config import LOGS_DIR

DB_PATH = LOGS_DIR / "telemetry.db"

def get_connection() -> sqlite3.Connection:
    """Returns a SQLite connection configured for concurrent WAL-mode access."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    """Initializes the telemetry database and tables."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS request_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                service TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                latency_ms REAL NOT NULL,
                is_error INTEGER NOT NULL,
                is_timeout INTEGER NOT NULL,
                error_detail TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_metrics_svc_time
            ON request_metrics(service, timestamp)
        """)
        conn.commit()

def record_metric(
    service: str,
    endpoint: str,
    method: str,
    status_code: int,
    latency_ms: float,
    is_error: bool,
    is_timeout: bool = False,
    error_detail: Optional[str] = None,
    timestamp: Optional[float] = None
):
    """Records a single real request metric row."""
    if timestamp is None:
        timestamp = time.time()

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO request_metrics (
                timestamp, service, endpoint, method,
                status_code, latency_ms, is_error, is_timeout, error_detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            service,
            endpoint,
            method,
            status_code,
            round(latency_ms, 2),
            1 if is_error else 0,
            1 if is_timeout else 0,
            error_detail
        ))
        conn.commit()

def calculate_percentile(values: List[float], percentile: float) -> float:
    """Calculates nearest-rank percentile from a sorted list of values."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (percentile / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(sorted_vals[int(k)], 2)
    d0 = sorted_vals[int(f)] * (c - k)
    d1 = sorted_vals[int(c)] * (k - f)
    return round(d0 + d1, 2)

def get_summary(service: str, window_seconds: Optional[float] = None) -> Dict[str, Any]:
    """Calculates structured telemetry summary for a specific service."""
    init_db()
    query = "SELECT * FROM request_metrics WHERE service = ?"
    params: List[Any] = [service]

    if window_seconds is not None:
        min_time = time.time() - window_seconds
        query += " AND timestamp >= ?"
        params.append(min_time)

    query += " ORDER BY timestamp ASC"

    with get_connection() as conn:
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

    if not rows:
        return {
            "service": service,
            "request_count": 0,
            "success_count": 0,
            "error_count": 0,
            "error_rate": 0.0,
            "avg_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "p99_latency_ms": 0.0,
            "timeout_count": 0,
            "status_distribution": {}
        }

    latencies = [row["latency_ms"] for row in rows]
    total_reqs = len(rows)
    error_count = sum(1 for row in rows if row["is_error"] == 1)
    timeout_count = sum(1 for row in rows if row["is_timeout"] == 1)
    success_count = total_reqs - error_count
    avg_latency = round(sum(latencies) / total_reqs, 2) if total_reqs > 0 else 0.0
    p95_latency = calculate_percentile(latencies, 95)
    p99_latency = calculate_percentile(latencies, 99)

    status_dist: Dict[int, int] = {}
    for row in rows:
        code = row["status_code"]
        status_dist[code] = status_dist.get(code, 0) + 1

    return {
        "service": service,
        "request_count": total_reqs,
        "success_count": success_count,
        "error_count": error_count,
        "error_rate": round(error_count / total_reqs, 4) if total_reqs > 0 else 0.0,
        "avg_latency_ms": avg_latency,
        "p95_latency_ms": p95_latency,
        "p99_latency_ms": p99_latency,
        "timeout_count": timeout_count,
        "status_distribution": status_dist
    }

def get_all_summaries(window_seconds: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """Calculates structured telemetry summaries across all registered services."""
    services = ["gateway", "order-service", "payment-service"]
    return {svc: get_summary(svc, window_seconds=window_seconds) for svc in services}

def clear_metrics():
    """Clears all records in the telemetry database (useful for test resets)."""
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM request_metrics")
        conn.commit()
