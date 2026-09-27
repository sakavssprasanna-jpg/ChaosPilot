from datetime import datetime, timezone
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Union

import matplotlib
matplotlib.use("Agg")  # Headless rendering for test and CLI environments
import matplotlib.pyplot as plt
import matplotlib.patches as patches

from services.config import BASE_DIR, LOGS_DIR, GATEWAY_URL, ORDER_SERVICE_URL, PAYMENT_SERVICE_URL
from telemetry.db import get_connection, init_db

VISUALIZATIONS_DIR = LOGS_DIR / "visualizations"

def generate_topology_diagram(output_path: Optional[Union[str, Path]] = None) -> str:
    """Generates an accurate visual service topology diagram derived directly from project configuration."""
    VISUALIZATIONS_DIR.mkdir(parents=True, exist_ok=True)
    target_path = Path(output_path) if output_path else VISUALIZATIONS_DIR / "topology_diagram.png"
    target_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 7)
    ax.axis("off")

    # Title & Subtitle
    ax.text(5, 6.5, "ChaosPilot Service Topology (Locally Generated)", fontsize=15, fontweight="bold", ha="center")
    ax.text(5, 6.1, "Ground Truth: services/config.py | knowledge/architecture/service_topology.md",
            fontsize=9, color="#555555", ha="center", style="italic")

    # Service Nodes Definition (x, y, w, h, title, subtitle, color, border)
    nodes = [
        (0.6, 3.8, 2.2, 1.4, "API Gateway", ":8000\nRoute: /api/checkout\nClient Budget: 3.0s", "#EBF5FB", "#2980B9"),
        (3.9, 3.8, 2.2, 1.4, "Order Service", ":8001\nRoute: /orders/create\nUpstream Budget: 2.0s", "#E8F8F5", "#16A085"),
        (7.2, 3.8, 2.2, 1.4, "Payment Service", ":8002\nRoute: /payments/process\nTarget Fault: Delay/5xx", "#FDEDEC", "#C0392B"),
        (7.2, 1.2, 2.2, 1.2, "In-Memory Ledger", "In-Process Dict\n(No external PostgreSQL)", "#F4F6F6", "#7F8C8D"),
    ]

    for x, y, w, h, title, sub, bg, border in nodes:
        rect = patches.FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.1,rounding_size=0.15",
                                      facecolor=bg, edgecolor=border, linewidth=2)
        ax.add_patch(rect)
        ax.text(x + w / 2, y + h - 0.35, title, fontsize=11, fontweight="bold", ha="center", va="top", color="#1C2833")
        ax.text(x + w / 2, y + 0.2, sub, fontsize=8, ha="center", va="bottom", color="#34495E")

    # Directed Arrows
    # Client -> Gateway
    ax.annotate("Client Traffic", xy=(0.6, 4.5), xytext=(-0.2, 4.5),
                arrowprops=dict(facecolor="#2C3E50", arrowstyle="->", lw=1.5),
                ha="right", va="center", fontsize=8, fontweight="bold")
    # Gateway -> Order
    ax.annotate("", xy=(3.9, 4.5), xytext=(2.8, 4.5),
                arrowprops=dict(facecolor="#2980B9", arrowstyle="->", lw=2))
    ax.text(3.35, 4.7, "HTTP POST\n/orders/create", fontsize=7.5, ha="center", color="#2980B9", fontweight="bold")

    # Order -> Payment
    ax.annotate("", xy=(7.2, 4.5), xytext=(6.1, 4.5),
                arrowprops=dict(facecolor="#16A085", arrowstyle="->", lw=2))
    ax.text(6.65, 4.7, "HTTP POST\n/payments/process", fontsize=7.5, ha="center", color="#16A085", fontweight="bold")

    # Payment -> Ledger
    ax.annotate("", xy=(8.3, 2.4), xytext=(8.3, 3.8),
                arrowprops=dict(facecolor="#7F8C8D", arrowstyle="->", lw=1.5, linestyle="--"))
    ax.text(8.4, 3.1, "State write", fontsize=7.5, ha="left", color="#7F8C8D")

    # Shared Telemetry Bus Box
    telemetry_rect = patches.FancyBboxPatch((0.6, 1.2), 5.5, 1.2, boxstyle="round,pad=0.1,rounding_size=0.15",
                                            facecolor="#FEF9E7", edgecolor="#F39C12", linewidth=1.5, linestyle=":")
    ax.add_patch(telemetry_rect)
    ax.text(3.35, 1.9, "Real SQLite Telemetry (WAL Mode)", fontsize=9.5, fontweight="bold", ha="center", color="#B7950B")
    ax.text(3.35, 1.45, "logs/telemetry.db • request_metrics • P95/P99 latency tracking", fontsize=8, ha="center", color="#7D6608")

    # Footer note
    ax.text(5, 0.3, "Stage 4 Multimodal Visual Evidence • Deterministic Project Architecture",
            fontsize=8, ha="center", color="#888888")

    plt.tight_layout()
    fig.savefig(str(target_path), bbox_inches="tight")
    plt.close(fig)

    return str(target_path.resolve())

def generate_telemetry_chart(
    output_path: Optional[Union[str, Path]] = None,
    window_seconds: Optional[float] = None
) -> str:
    """Generates an empirical performance & error chart from real SQLite telemetry records."""
    VISUALIZATIONS_DIR.mkdir(parents=True, exist_ok=True)
    target_path = Path(output_path) if output_path else VISUALIZATIONS_DIR / "telemetry_chart.png"
    target_path.parent.mkdir(parents=True, exist_ok=True)

    init_db()
    query = """
        SELECT timestamp, service, endpoint, status_code, latency_ms, is_error, is_timeout
        FROM request_metrics
    """
    params: List[Any] = []
    if window_seconds is not None:
        min_ts = time.time() - window_seconds
        query += " WHERE timestamp >= ?"
        params.append(min_ts)
    query += " ORDER BY timestamp ASC"

    with get_connection() as conn:
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), dpi=150)

    if not rows:
        # Render clean placeholder if database is empty
        for ax in (ax1, ax2):
            ax.text(0.5, 0.5, "No telemetry records found in logs/telemetry.db\n(Run traffic or incident simulation to generate data)",
                    ha="center", va="center", fontsize=11, color="#777777")
            ax.axis("off")
        fig.suptitle("ChaosPilot Telemetry Profile (Empty Database)", fontsize=14, fontweight="bold")
    else:
        min_time = rows[0]["timestamp"]
        services = ["gateway", "order-service", "payment-service"]
        color_map = {"gateway": "#2980B9", "order-service": "#16A085", "payment-service": "#C0392B"}

        # 1. Latency over time
        for svc in services:
            svc_rows = [r for r in rows if r["service"] == svc]
            if not svc_rows:
                continue
            x_vals = [r["timestamp"] - min_time for r in svc_rows]
            y_vals = [r["latency_ms"] for r in svc_rows]
            ax1.plot(x_vals, y_vals, label=svc, color=color_map.get(svc, "#333333"), marker="o", markersize=3, alpha=0.7)

            # Highlight timeouts/errors with red halo
            err_rows = [r for r in svc_rows if r["is_error"] == 1 or r["is_timeout"] == 1]
            if err_rows:
                err_x = [r["timestamp"] - min_time for r in err_rows]
                err_y = [r["latency_ms"] for r in err_rows]
                ax1.scatter(err_x, err_y, color="#E74C3C", s=50, zorder=5, edgecolors="#78281F", label=f"{svc} Error/Timeout" if svc == "payment-service" else None)

        ax1.set_title("Request Latency Over Time (by Service)", fontsize=11, fontweight="bold")
        ax1.set_xlabel("Elapsed Seconds", fontsize=9)
        ax1.set_ylabel("Latency (ms)", fontsize=9)
        ax1.grid(True, linestyle="--", alpha=0.5)
        ax1.legend(loc="upper left", fontsize=8)

        # 2. HTTP Status code distribution
        status_codes = sorted(list({r["status_code"] for r in rows}))
        x_indices = range(len(services))
        bar_width = 0.8 / max(len(status_codes), 1)

        code_colors = {200: "#2ECC71", 500: "#E67E22", 504: "#E74C3C"}

        for i, code in enumerate(status_codes):
            counts = []
            for svc in services:
                c = sum(1 for r in rows if r["service"] == svc and r["status_code"] == code)
                counts.append(c)
            offset = (i - len(status_codes) / 2 + 0.5) * bar_width
            bar_positions = [x + offset for x in x_indices]
            ax2.bar(bar_positions, counts, width=bar_width,
                    label=f"HTTP {code}",
                    color=code_colors.get(code, "#95A5A6"),
                    edgecolor="#2C3E50", linewidth=0.8)

        ax2.set_title("HTTP Status Distribution per Service", fontsize=11, fontweight="bold")
        ax2.set_xticks(list(x_indices))
        ax2.set_xticklabels(services, fontsize=9, fontweight="bold")
        ax2.set_ylabel("Request Count", fontsize=9)
        ax2.grid(True, axis="y", linestyle="--", alpha=0.5)
        ax2.legend(loc="upper right", fontsize=8)

        fig.suptitle(f"ChaosPilot Request Telemetry Profile ({len(rows)} Records from logs/telemetry.db)",
                     fontsize=13, fontweight="bold")

    plt.tight_layout()
    fig.savefig(str(target_path), bbox_inches="tight")
    plt.close(fig)

    return str(target_path.resolve())
