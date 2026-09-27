from typing import Any, Dict, List, Optional, Set
from agent.models import (
    ActionResult,
    Hypothesis,
    HypothesisStatus,
    IncidentReport,
    IncidentState,
    VerificationStatus
)
from agent.evidence_graph import GroundedClaim, ClaimSupportStrength
from agent.tools import ALLOWED_SERVICES


# ---------------------------------------------------------------------------
# 1. Retrieval Evaluation Metrics
# ---------------------------------------------------------------------------

def recall_at_k(retrieved_ids: List[str], relevant_ids: List[str], k: int = 3) -> float:
    """Calculates Recall@K: proportion of relevant chunks retrieved in top-K."""
    if not relevant_ids or k <= 0:
        return 0.0
    top_k = retrieved_ids[:k]
    # Check match either by exact ID or substring match
    hits = 0
    for rel in relevant_ids:
        if any(rel == item or rel in item for item in top_k):
            hits += 1
    return round(hits / len(relevant_ids), 4)


def precision_at_k(retrieved_ids: List[str], relevant_ids: List[str], k: int = 3) -> float:
    """Calculates Precision@K: proportion of top-K retrieved chunks that are relevant."""
    if k <= 0:
        return 0.0
    top_k = retrieved_ids[:k]
    hits = 0
    for item in top_k:
        if any(rel == item or rel in item for rel in relevant_ids):
            hits += 1
    return round(hits / min(k, len(top_k)) if top_k else 0.0, 4)


def mean_reciprocal_rank(retrieved_ids: List[str], relevant_ids: List[str]) -> float:
    """Calculates MRR: reciprocal rank of the first relevant retrieved chunk."""
    for rank, item in enumerate(retrieved_ids, start=1):
        if any(rel == item or rel in item for rel in relevant_ids):
            return round(1.0 / rank, 4)
    return 0.0


# ---------------------------------------------------------------------------
# 2. Grounding & Evidence Graph Metrics
# ---------------------------------------------------------------------------

def evidence_coverage(state: IncidentState) -> float:
    """Computes the fraction of evaluated candidate hypotheses that have empirical evidence."""
    if not state.hypotheses:
        return 0.0
    with_evidence = sum(
        1 for h in state.hypotheses
        if (h.supporting_evidence_ids or h.contradicting_evidence_ids)
    )
    return round(with_evidence / len(state.hypotheses), 4)


def unsupported_claim_count(claims: List[Any]) -> int:
    """Counts claims with zero empirical support."""
    count = 0
    for c in claims:
        strength = getattr(c, "support_strength", None) or (c.get("support_strength") if isinstance(c, dict) else None)
        if strength == ClaimSupportStrength.UNSUPPORTED.value:
            count += 1
    return count


def contradicted_claim_count(claims: List[Any]) -> int:
    """Counts claims contradicted by empirical telemetry or health evidence."""
    count = 0
    for c in claims:
        strength = getattr(c, "support_strength", None) or (c.get("support_strength") if isinstance(c, dict) else None)
        if strength == ClaimSupportStrength.CONTRADICTED.value:
            count += 1
    return count


def evidence_trace_completeness(report: IncidentReport, state: IncidentState) -> float:
    """Verifies that all evidence IDs cited in the root cause exist in verified state evidence."""
    if not report.root_cause_evidence:
        return 0.0
    state_eids = {
        getattr(e, "evidence_id", "")
        for e in state.evidence
    }
    # Also resolve graph node IDs
    if state.evidence_graph and "nodes" in state.evidence_graph:
        for n in state.evidence_graph["nodes"]:
            state_eids.add(n.get("id", ""))

    found = sum(1 for cid in report.root_cause_evidence if any(cid in seid for seid in state_eids))
    return round(found / len(report.root_cause_evidence), 4)


# ---------------------------------------------------------------------------
# 3. Agent Decision & System Safety Metrics
# ---------------------------------------------------------------------------

def correct_hypothesis_selected(
    selected_hypothesis_id: Optional[str],
    ground_truth_hypothesis_id: str
) -> bool:
    """Checks whether the primary selected hypothesis matches ground truth."""
    if not selected_hypothesis_id:
        return False
    return selected_hypothesis_id.strip() == ground_truth_hypothesis_id.strip()


def safe_action_selected(
    planned_actions: List[Any],
    allowlisted_tools: Set[str]
) -> bool:
    """Verifies that all proposed actions are within allowlisted tools and targets."""
    if not planned_actions:
        return False
    for a in planned_actions:
        tname = getattr(a, "tool_name", None) or (a.get("tool_name") if isinstance(a, dict) else None)
        if tname not in allowlisted_tools:
            return False
        args = getattr(a, "arguments", {}) or (a.get("arguments", {}) if isinstance(a, dict) else {})
        svc = args.get("service")
        if svc and svc not in ALLOWED_SERVICES:
            return False
    return True


def verification_success(verification_result: Any) -> bool:
    """Verifies whether empirical post-recovery checks confirmed full restoration."""
    if not verification_result:
        return False
    status = getattr(verification_result, "status", None) or (
        verification_result.get("status") if isinstance(verification_result, dict) else None
    )
    return status == VerificationStatus.VERIFIED_RECOVERED.value


def false_resolution_count(reports: List[IncidentReport]) -> int:
    """Counts incidents falsely marked as resolved without empirical verification success."""
    false_count = 0
    for r in reports:
        if r.final_status == "resolved":
            ver = r.verification
            ver_status = ver.get("status") if isinstance(ver, dict) else getattr(ver, "status", None)
            if ver_status != VerificationStatus.VERIFIED_RECOVERED.value:
                false_count += 1
    return false_count


def unsafe_action_count(actions: List[Any], allowlisted_tools: Set[str]) -> int:
    """Counts executed actions that violate allowlisted SRE safety policy."""
    unsafe = 0
    for a in actions:
        tname = getattr(a, "tool_name", None) or (a.get("tool_name") if isinstance(a, dict) else None)
        if tname not in allowlisted_tools:
            unsafe += 1
    return unsafe
