import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Union

from rag.models import EvidenceBundle, EvidenceCitation, GitEvidenceCitation
from rag.retriever import get_retriever, retrieve, retrieve_multi

class EvidenceCoordinator:
    """Coordinates multi-modal evidence collection across runbooks, architecture specs, postmortems, and Git diffs."""

    def __init__(self):
        from git_ops.diff_analyzer import get_git_analyzer
        self.retriever = get_retriever()
        self.git_analyzer = get_git_analyzer()

    def coordinate_evidence(
        self,
        query: str,
        top_k_docs: int = 5,
        top_k_git: int = 3
    ) -> EvidenceBundle:
        """Retrieves structured evidence across all knowledge types and Git commits for a given query."""
        doc_citations = self.retriever.retrieve(query, top_k=top_k_docs)

        # Extract terms for Git search
        query_terms = [t for t in query.lower().split() if len(t) > 2]
        git_citations = self.git_analyzer.search_commits(query_terms, limit=top_k_git)

        sources = []
        for d in doc_citations:
            if d.document not in sources:
                sources.append(d.document)
        for g in git_citations:
            src = f"git:{g.commit}"
            if src not in sources:
                sources.append(src)

        return EvidenceBundle(
            query=query,
            documents=doc_citations,
            git_evidence=git_citations,
            sources=sources,
            timestamp=time.time()
        )

    def diagnose_from_snapshot(
        self,
        snapshot: Union[str, Path, Dict[str, Any]],
        top_k_docs: int = 6,
        top_k_git: int = 3
    ) -> EvidenceBundle:
        """Inspects real Stage 2 incident snapshot data, derives diagnostic terms, and retrieves relevant evidence."""
        # Load snapshot if file path provided
        if isinstance(snapshot, (str, Path)):
            with open(snapshot, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = snapshot

        # 1. Inspect affected services
        affected_services = []
        service_health = data.get("service_health", {})
        for svc_name, h in service_health.items():
            st = h.get("status", "")
            if st in ("degraded", "down", "timeout", "unhealthy") or h.get("details", {}).get("fault_active"):
                affected_services.append(svc_name)

        # 2. Inspect fault mode
        fault_status = data.get("fault_status", {})
        fault_mode = fault_status.get("mode", "")
        fault_desc = fault_status.get("description", "")

        # 3. Inspect telemetry for status codes and timeouts
        has_504 = False
        metrics_summary = data.get("metrics_summary", {})
        for svc, m in metrics_summary.items():
            dist = m.get("status_distribution", {})
            if "504" in dist or 504 in dist or m.get("timeout_count", 0) > 0:
                has_504 = True

        # 4. Formulate diagnostic queries
        diagnostic_queries: List[str] = []
        terms: List[str] = []

        if "payment-service" in affected_services or fault_mode == "timeout":
            diagnostic_queries.append("payment service timeout failure recovery")
            terms.extend(["payment", "timeout", "pool"])

        if has_504 or "gateway" in affected_services:
            diagnostic_queries.append("gateway 504 upstream timeout mitigation")
            terms.extend(["gateway", "504", "upstream"])

        if fault_desc:
            diagnostic_queries.append(fault_desc)
            terms.extend(fault_desc.split())

        # Fallback query if no specific flags
        if not diagnostic_queries:
            diagnostic_queries.append("microservice timeout circuit breakdown diagnosis")
            terms.extend(["service", "timeout", "health"])

        combined_query = " | ".join(diagnostic_queries)

        # Retrieve documents via multi-query
        doc_citations = self.retriever.retrieve_multi(diagnostic_queries, top_k=top_k_docs)

        # Search Git commits using derived terms
        git_citations = self.git_analyzer.search_commits(terms, limit=top_k_git)

        sources = []
        for d in doc_citations:
            if d.document not in sources:
                sources.append(d.document)
        for g in git_citations:
            src = f"git:{g.commit}"
            if src not in sources:
                sources.append(src)

        return EvidenceBundle(
            query=combined_query,
            documents=doc_citations,
            git_evidence=git_citations,
            sources=sources,
            timestamp=time.time()
        )

_evidence_coordinator_instance: Optional[EvidenceCoordinator] = None

def get_evidence_coordinator() -> EvidenceCoordinator:
    global _evidence_coordinator_instance
    if _evidence_coordinator_instance is None:
        _evidence_coordinator_instance = EvidenceCoordinator()
    return _evidence_coordinator_instance

def coordinate_evidence(query: str, top_k_docs: int = 5, top_k_git: int = 3) -> EvidenceBundle:
    return get_evidence_coordinator().coordinate_evidence(query, top_k_docs=top_k_docs, top_k_git=top_k_git)

def diagnose_evidence(snapshot: Union[str, Path, Dict[str, Any]]) -> EvidenceBundle:
    return get_evidence_coordinator().diagnose_from_snapshot(snapshot)
