from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from multimodal.schema import (
    MultimodalEvidenceItem,
    MultimodalEvidenceBundle,
    VoiceTranscript,
    VisualInspectionResult,
    utc_now_iso
)
from rag.retriever import retrieve
from git_ops.diff_analyzer import get_git_analyzer

class EvidenceCorrelator:
    """Correlates real telemetry, retrieved runbooks, git diffs, and multimodal perception items."""

    def __init__(self):
        self._git_analyzer = None

    @property
    def git_analyzer(self):
        if self._git_analyzer is None:
            self._git_analyzer = get_git_analyzer()
        return self._git_analyzer

    def correlate_telemetry(
        self,
        snapshot: Dict[str, Any],
        source: str = "logs/telemetry.db"
    ) -> List[MultimodalEvidenceItem]:
        """Deconstructs real incident snapshot data into granular telemetry evidence items."""
        items: List[MultimodalEvidenceItem] = []
        now_str = snapshot.get("timestamp_iso", utc_now_iso())

        # 1. Overall & Service Health
        service_health = snapshot.get("service_health", {})
        overall_status = snapshot.get("overall_status", "unknown")
        items.append(MultimodalEvidenceItem(
            evidence_id=f"obs_health_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
            modality="telemetry",
            source=source,
            timestamp=now_str,
            content={
                "overall_status": overall_status,
                "service_health": service_health
            },
            provenance=f"health_probe:{source}",
            status="verified",
            metadata={"type": "service_health", "overall_status": overall_status}
        ))

        # 2. Metrics summary per service
        metrics_summary = snapshot.get("metrics_summary", {})
        for svc_name, metrics in metrics_summary.items():
            if not isinstance(metrics, dict):
                continue
            req_count = metrics.get("request_count", 0)
            err_count = metrics.get("error_count", 0)
            timeout_count = metrics.get("timeout_count", 0)
            p95 = metrics.get("p95_latency_ms", 0.0)
            status_dist = metrics.get("status_distribution", {})

            items.append(MultimodalEvidenceItem(
                evidence_id=f"obs_metrics_{svc_name}_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
                modality="telemetry",
                source=source,
                timestamp=now_str,
                content={
                    "service": svc_name,
                    "request_count": req_count,
                    "error_count": err_count,
                    "error_rate": metrics.get("error_rate", 0.0),
                    "timeout_count": timeout_count,
                    "p95_latency_ms": p95,
                    "status_distribution": status_dist
                },
                provenance=f"metrics_summary:{svc_name}@{source}",
                status="verified",
                metadata={"type": "service_metrics", "service": svc_name}
            ))

        # 3. Fault status if present
        fault_status = snapshot.get("fault_status", {})
        if fault_status and fault_status.get("active"):
            items.append(MultimodalEvidenceItem(
                evidence_id=f"obs_fault_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
                modality="telemetry",
                source="payment-service:/fault/status",
                timestamp=now_str,
                content=fault_status,
                provenance="http://127.0.0.1:8002/fault/status",
                status="verified",
                metadata={"type": "fault_injection_active", "mode": fault_status.get("mode")}
            ))

        return items

    def correlate_knowledge(
        self,
        query: str,
        top_k: int = 4
    ) -> List[MultimodalEvidenceItem]:
        """Retrieves grounded runbook, architecture, and postmortem evidence chunks."""
        citations = retrieve(query, top_k=top_k)
        items: List[MultimodalEvidenceItem] = []

        for c in citations:
            items.append(MultimodalEvidenceItem(
                evidence_id=f"rag_{c.chunk_id}",
                modality="text",
                source=c.document,
                timestamp=utc_now_iso(),
                content=c.text_snippet,
                provenance=f"{c.document}#{c.section} (score={c.score:.3f})",
                status="verified",
                metadata={
                    "chunk_id": c.chunk_id,
                    "section": c.section,
                    "score": c.score,
                    "source_type": c.source_type
                }
            ))

        return items

    def correlate_git(
        self,
        query_terms: List[str],
        limit: int = 3
    ) -> List[MultimodalEvidenceItem]:
        """Retrieves isolated git commit evidence only if genuinely relevant to query terms."""
        commits = self.git_analyzer.search_commits(query_terms, limit=limit)
        items: List[MultimodalEvidenceItem] = []

        for g in commits:
            items.append(MultimodalEvidenceItem(
                evidence_id=f"git_{g.commit[:8]}",
                modality="git_diff",
                source=f"git_commit:{g.commit[:8]}",
                timestamp=g.date,
                content=f"Commit: {g.commit}\nAuthor: {g.author}\nMessage: {g.message}\nFiles: {', '.join(g.files_changed)}\nDiff:\n{g.diff[:500]}",
                provenance=f"git_history:{g.commit} by {g.author}",
                status="verified",
                metadata={
                    "commit": g.commit,
                    "author": g.author,
                    "relevance": g.relevance,
                    "files_changed": g.files_changed
                }
            ))

        return items
