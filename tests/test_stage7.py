import json
from pathlib import Path
import re
from typing import Any, Dict, List
import pytest

from agent.evidence_graph import (
    ClaimStatus,
    ClaimSupportStrength,
    EdgeType,
    EvidenceGraph,
    EvidenceGraphBuilder,
    GraphEdge,
    GraphNode,
    GroundedClaim,
    NodeType
)
from agent.hypotheses import HypothesisEngine
from agent.models import (
    ConfidenceLevel,
    Hypothesis,
    HypothesisStatus,
    IncidentPhase,
    IncidentReport,
    IncidentState,
    VerificationResult,
    VerificationStatus,
    utc_now_iso
)
from agent.orchestrator import IncidentOrchestrator
from agent.reasoning import DeterministicReasoningBackend, GeminiReasoningBackend
from agent.tools import ToolRegistry, ALLOWED_SERVICES
from app.streamlit_app import should_display_resolved
from evaluation.benchmarks import SyntheticControlledBenchmarkFixture, get_default_benchmark_fixture
from evaluation.framework import SystemEvaluator, BenchmarkComparisonResult
from evaluation.metrics import (
    recall_at_k,
    precision_at_k,
    mean_reciprocal_rank,
    evidence_coverage,
    unsupported_claim_count,
    contradicted_claim_count,
    evidence_trace_completeness,
    correct_hypothesis_selected,
    safe_action_selected,
    verification_success,
    false_resolution_count,
    unsafe_action_count
)
from multimodal.schema import MultimodalEvidenceItem
from multimodal.vision_inspector import VisionInspector
from multimodal.visualizer import generate_topology_diagram
from services.config import LOGS_DIR


# ---------------------------------------------------------------------------
# Test 1: Evidence Graph Construction
# ---------------------------------------------------------------------------

def test_evidence_graph_construction():
    """Verify evidence graph can be constructed with typed nodes and edges."""
    graph = EvidenceGraph()
    n1 = GraphNode(id="svc:gateway", type=NodeType.SERVICE.value, label="API Gateway")
    n2 = GraphNode(id="svc:payment-service", type=NodeType.SERVICE.value, label="Payment Service")
    edge = GraphEdge(source="svc:gateway", target="svc:payment-service", relationship=EdgeType.DEPENDS_ON.value)

    graph.add_node(n1)
    graph.add_node(n2)
    graph.add_edge(edge)

    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    assert graph.get_node("svc:gateway") is not None
    assert graph.get_node("svc:payment-service") is not None


# ---------------------------------------------------------------------------
# Test 2: Stable Node IDs
# ---------------------------------------------------------------------------

def test_stable_node_ids():
    """Verify nodes generated for identical components have deterministic IDs."""
    state = IncidentState(incident_id="inc_fixed_123", started_at="2026-09-27T00:00:00Z")
    builder = EvidenceGraphBuilder()
    g1 = builder.build_graph(state)
    g2 = builder.build_graph(state)

    assert "svc:gateway" in g1.nodes
    assert "svc:order-service" in g1.nodes
    assert "svc:payment-service" in g1.nodes
    assert set(g1.nodes.keys()) == set(g2.nodes.keys())


# ---------------------------------------------------------------------------
# Test 3: Edge Provenance
# ---------------------------------------------------------------------------

def test_edge_provenance():
    """Verify edges retain relationship semantics and optional provenance."""
    edge = GraphEdge(
        source="fault:payment_timeout",
        target="svc:payment-service",
        relationship=EdgeType.OBSERVED_ON.value,
        provenance="http://127.0.0.1:8002/fault/status"
    )
    assert edge.relationship == "observed_on"
    assert edge.provenance == "http://127.0.0.1:8002/fault/status"
    d = edge.to_dict()
    assert d["source"] == "fault:payment_timeout"
    assert d["target"] == "svc:payment-service"


# ---------------------------------------------------------------------------
# Test 4: Graph Serialization (to_dict, to_json, from_dict)
# ---------------------------------------------------------------------------

def test_graph_serialization():
    """Verify EvidenceGraph serializes to dict/json and deserializes losslessly."""
    graph = EvidenceGraph()
    graph.add_node(GraphNode(id="svc:gateway", type="service", label="Gateway"))
    graph.add_node(GraphNode(id="svc:order", type="service", label="Order"))
    graph.add_edge(GraphEdge("svc:gateway", "svc:order", "depends_on"))
    graph.add_claim(GroundedClaim(
        claim_id="claim_01",
        statement="Gateway routes to Order",
        evidence_ids=["svc:gateway"],
        support_strength="supported",
        status="confirmed"
    ))

    data = graph.to_dict()
    assert data["node_count"] == 3  # gateway, order, claim:claim_01
    assert data["edge_count"] >= 1
    assert data["claim_count"] == 1

    json_str = graph.to_json()
    assert isinstance(json_str, str)
    assert "claim:claim_01" in json_str

    restored = EvidenceGraph.from_dict(data)
    assert len(restored.nodes) == len(graph.nodes)
    assert len(restored.edges) == len(graph.edges)
    assert "claim_01" in restored.claims


# ---------------------------------------------------------------------------
# Test 5: Grounded Claim Creation
# ---------------------------------------------------------------------------

def test_grounded_claim_creation():
    """Verify GroundedClaim fields, support strength enum, and serialization."""
    claim = GroundedClaim(
        claim_id="claim_payment_timeout",
        statement="Payment delay exceeded upstream budget.",
        evidence_ids=["obs_tel_01", "obs_rag_02"],
        support_strength=ClaimSupportStrength.STRONGLY_SUPPORTED.value,
        status=ClaimStatus.CONFIRMED.value,
        contradictions=[],
        created_at=utc_now_iso()
    )
    assert claim.support_strength == "strongly_supported"
    assert claim.status == "confirmed"
    assert len(claim.evidence_ids) == 2
    assert len(claim.contradictions) == 0


# ---------------------------------------------------------------------------
# Test 6: Supporting Evidence Querying
# ---------------------------------------------------------------------------

def test_supporting_evidence_querying():
    """Verify EvidenceGraph correctly answers 'What evidence supports this claim?'."""
    graph = EvidenceGraph()
    graph.add_node(GraphNode(id="tel:payment_latency", type="telemetry", label="Latency 4500ms"))
    graph.add_node(GraphNode(id="doc:runbook_sla", type="document", label="Runbook Budget 2.0s"))
    graph.add_claim(GroundedClaim(
        claim_id="payment_timeout_claim",
        statement="Payment service timed out.",
        evidence_ids=["tel:payment_latency", "doc:runbook_sla"],
        support_strength="supported",
        status="confirmed"
    ))

    supporting = graph.get_supporting_evidence("payment_timeout_claim")
    assert len(supporting) == 2
    node_ids = {n.id for n in supporting}
    assert "tel:payment_latency" in node_ids
    assert "doc:runbook_sla" in node_ids


# ---------------------------------------------------------------------------
# Test 7: Contradicting Evidence Querying
# ---------------------------------------------------------------------------

def test_contradicting_evidence_querying():
    """Verify EvidenceGraph correctly answers 'What evidence contradicts this claim?'."""
    graph = EvidenceGraph()
    graph.add_node(GraphNode(id="tel:order_healthy", type="telemetry", label="Order HTTP 200 (Errors: 0)"))
    graph.add_claim(GroundedClaim(
        claim_id="order_crashed_claim",
        statement="Order service crashed.",
        evidence_ids=[],
        support_strength="contradicted",
        status="refuted",
        contradictions=["tel:order_healthy"]
    ))

    contradicting = graph.get_contradicting_evidence("order_crashed_claim")
    assert len(contradicting) == 1
    assert contradicting[0].id == "tel:order_healthy"


# ---------------------------------------------------------------------------
# Test 8: Unsupported Claim Detection
# ---------------------------------------------------------------------------

def test_unsupported_claim_detection():
    """Verify claims lacking empirical backing are detected as unsupported."""
    claim_unsupported = GroundedClaim(
        claim_id="claim_database_deadlock",
        statement="PostgreSQL deadlock occurred.",
        evidence_ids=[],
        support_strength=ClaimSupportStrength.UNSUPPORTED.value,
        status=ClaimStatus.CANDIDATE.value
    )
    claim_supported = GroundedClaim(
        claim_id="claim_payment_timeout",
        statement="Payment timeout occurred.",
        evidence_ids=["ev_1"],
        support_strength=ClaimSupportStrength.SUPPORTED.value,
        status=ClaimStatus.CONFIRMED.value
    )

    claims = [claim_unsupported, claim_supported]
    assert unsupported_claim_count(claims) == 1


# ---------------------------------------------------------------------------
# Test 9: Multimodal Evidence Correlation in Graph
# ---------------------------------------------------------------------------

def test_multimodal_evidence_correlation_in_graph():
    """Verify graph interconnects telemetry, runbook text, git, visual, and voice items."""
    state = IncidentState(incident_id="inc_multi_01", started_at=utc_now_iso())
    state.evidence = [
        MultimodalEvidenceItem(
            evidence_id="tel_gw_504",
            modality="telemetry",
            source="logs/telemetry.db",
            timestamp=utc_now_iso(),
            content={"service": "gateway", "status_distribution": {"504": 5}, "error_count": 5},
            provenance="metrics_summary:gateway@logs/telemetry.db"
        ),
        MultimodalEvidenceItem(
            evidence_id="rag_chunk_01",
            modality="text",
            source="knowledge/runbooks/payment_timeout.md",
            timestamp=utc_now_iso(),
            content="Payment latency exceeding 2.0s triggers Gateway 504 timeout propagation.",
            provenance="knowledge/runbooks/payment_timeout.md#Timeout Budget"
        ),
        MultimodalEvidenceItem(
            evidence_id="git_commit_01",
            modality="git_diff",
            source="git_history",
            timestamp=utc_now_iso(),
            content="Commit: chaospilot timeout update",
            provenance="git_history:commit_abc123"
        ),
        MultimodalEvidenceItem(
            evidence_id="vis_chart_01",
            modality="visual_observation",
            source="telemetry_chart.png",
            timestamp=utc_now_iso(),
            content="Spike in 504 status codes.",
            provenance="visualizer:telemetry_chart.png"
        ),
        MultimodalEvidenceItem(
            evidence_id="voice_input_01",
            modality="voice_transcript",
            source="mic",
            timestamp=utc_now_iso(),
            content="Payment service is failing with timeouts.",
            provenance="stt_audio:mic"
        )
    ]

    builder = EvidenceGraphBuilder()
    graph = builder.build_graph(state)

    modalities = {n.type for n in graph.nodes.values()}
    assert NodeType.TELEMETRY.value in modalities
    assert NodeType.DOCUMENT.value in modalities
    assert NodeType.GIT_CHANGE.value in modalities
    assert NodeType.VISUAL_OBSERVATION.value in modalities
    assert NodeType.VOICE_OBSERVATION.value in modalities


# ---------------------------------------------------------------------------
# Test 10: Hypothesis Grounding in Graph
# ---------------------------------------------------------------------------

def test_hypothesis_grounding_in_graph():
    """Verify hypothesis nodes link to supporting claims and empirical evidence."""
    state = IncidentState(incident_id="inc_hyp_01", started_at=utc_now_iso())
    hyp = Hypothesis(
        id=HypothesisEngine.HYPOTHESIS_PAYMENT_TIMEOUT,
        statement="Payment timeout budget exceeded.",
        supporting_evidence_ids=["tel_pay_01", "rag_doc_01"],
        confidence=ConfidenceLevel.HIGH.value,
        status=HypothesisStatus.SUPPORTED.value
    )
    state.hypotheses = [hyp]

    builder = EvidenceGraphBuilder()
    graph = builder.build_graph(state)

    hyp_node = graph.get_node(f"hyp:{hyp.id}")
    assert hyp_node is not None
    assert hyp_node.type == NodeType.HYPOTHESIS.value


# ---------------------------------------------------------------------------
# Test 11: Deterministic Reasoning without Gemini
# ---------------------------------------------------------------------------

def test_deterministic_reasoning_without_gemini():
    """Verify deterministic reasoning identifies payment timeout without cloud LLM."""
    backend = DeterministicReasoningBackend()
    candidates = HypothesisEngine().generate_candidate_hypotheses()

    evidence = [
        MultimodalEvidenceItem(
            evidence_id="ev_fault",
            modality="telemetry",
            source="fault/status",
            timestamp=utc_now_iso(),
            content={"active": True, "mode": "timeout", "delay_seconds": 4.5},
            provenance="http://127.0.0.1:8002/fault/status"
        ),
        MultimodalEvidenceItem(
            evidence_id="ev_metrics_gw",
            modality="telemetry",
            source="telemetry.db",
            timestamp=utc_now_iso(),
            content={"service": "gateway", "error_count": 5, "status_distribution": {"504": 5}},
            provenance="metrics_summary:gateway@telemetry.db"
        )
    ]

    res = backend.reason(evidence, candidates)
    assert res["backend"] == "deterministic"
    assert res["primary_hypothesis"] is not None
    assert res["primary_hypothesis"].id == HypothesisEngine.HYPOTHESIS_PAYMENT_TIMEOUT
    assert res["primary_hypothesis"].status == HypothesisStatus.SUPPORTED.value


# ---------------------------------------------------------------------------
# Test 12: Gemini Unavailable Behavior
# ---------------------------------------------------------------------------

def test_gemini_unavailable_behavior(monkeypatch):
    """Verify GeminiReasoningBackend cleanly falls back when API key is missing."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    backend = GeminiReasoningBackend()
    assert backend.has_credentials is False

    candidates = HypothesisEngine().generate_candidate_hypotheses()
    res = backend.reason([], candidates)

    assert "deterministic" in res["backend"]
    assert "llm_analysis" not in res  # Never fakes LLM output


# ---------------------------------------------------------------------------
# Test 13: No Fake Semantic Vision
# ---------------------------------------------------------------------------

def test_no_fake_semantic_vision(monkeypatch):
    """Verify VisionInspector reports unavailable rather than fabricating semantic vision."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    top_path = LOGS_DIR / "visualizations" / "test_stage7_topo.png"
    top_path.parent.mkdir(parents=True, exist_ok=True)
    generate_topology_diagram(top_path)

    inspector = VisionInspector()
    res = inspector.inspect(top_path)

    # When no key is set, status is unavailable or local structural
    assert res.status in ("unavailable", "success")
    if res.backend == "none":
        assert res.status == "unavailable"
        assert "not configured" in (res.error or "")


# ---------------------------------------------------------------------------
# Test 14: Evaluation Metrics Calculation
# ---------------------------------------------------------------------------

def test_evaluation_metrics_calculation():
    """Verify Recall@K, Precision@K, MRR, and trace completeness mathematics."""
    retrieved = ["chunk_A", "chunk_B", "chunk_C"]
    relevant = ["chunk_B", "chunk_D"]

    # Recall@3: 1 found out of 2 relevant = 0.5
    rec = recall_at_k(retrieved, relevant, k=3)
    assert rec == 0.5

    # Precision@3: 1 relevant out of 3 retrieved = 0.3333
    prec = precision_at_k(retrieved, relevant, k=3)
    assert prec == 0.3333

    # MRR: first hit is at rank 2 -> 1/2 = 0.5
    mrr = mean_reciprocal_rank(retrieved, relevant)
    assert mrr == 0.5


# ---------------------------------------------------------------------------
# Test 15: Synthetic Controlled Benchmark Fixture
# ---------------------------------------------------------------------------

def test_synthetic_controlled_benchmark_fixture():
    """Verify benchmark fixture contains transparent labeling and ground truth."""
    fixture = get_default_benchmark_fixture()
    assert isinstance(fixture, SyntheticControlledBenchmarkFixture)
    assert fixture.is_synthetic_controlled is True
    assert "Controlled microservice sandbox" in fixture.limitations
    assert fixture.ground_truth_hypothesis_id == HypothesisEngine.HYPOTHESIS_PAYMENT_TIMEOUT
    assert fixture.ground_truth_remediation_tool == "reset_controlled_fault"


# ---------------------------------------------------------------------------
# Test 16: Baseline vs Advanced Evaluation Comparison
# ---------------------------------------------------------------------------

def test_baseline_vs_advanced_evaluation_comparison():
    """Verify SystemEvaluator compares Config A, B, C, D with measurable differences."""
    evaluator = SystemEvaluator()
    res = evaluator.evaluate_all()

    assert isinstance(res, BenchmarkComparisonResult)
    assert len(res.configurations) == 4

    ca = res.configurations["Config_A_Retrieval_Only"]
    cb = res.configurations["Config_B_RAG_Deterministic_Reasoning"]
    cc = res.configurations["Config_C_Agentic_RAG_Verification"]
    cd = res.configurations["Config_D_Multimodal_EvidenceGraph_Verification"]

    # Config A cannot diagnose hypothesis
    assert ca["hypothesis_correctness"] is False
    assert ca["safe_action_executed"] is False
    assert ca["final_status"] == "unresolved"

    # Config B diagnoses hypothesis but executes no actions
    assert cb["hypothesis_correctness"] is True
    assert cb["safe_action_executed"] is False

    # Config C executes actions and verifies
    assert cc["safe_action_executed"] is True
    assert cc["empirically_verified"] is True
    assert cc["final_status"] == "resolved"

    # Config D constructs evidence graph and validates trace completeness
    assert cd["safe_action_executed"] is True
    assert cd["empirically_verified"] is True
    assert cd["evidence_trace_completeness"] == 1.0
    assert cd["evidence_graph_nodes"] > 15
    assert cd["grounded_claims_count"] >= 3


# ---------------------------------------------------------------------------
# Test 17: Mermaid Graph Export
# ---------------------------------------------------------------------------

def test_mermaid_graph_export():
    """Verify to_mermaid produces valid flowchart without syntax-breaking symbols."""
    graph = EvidenceGraph()
    graph.add_node(GraphNode("svc:payment", "service", "Payment Service (:8002)"))
    graph.add_node(GraphNode("fault:timeout", "fault", "Fault: Timeout (4.5s delay)"))
    graph.add_edge(GraphEdge("fault:timeout", "svc:payment", "observed_on"))

    mermaid = graph.to_mermaid(direction="TD")
    assert mermaid.startswith("flowchart TD")
    assert "fault_timeout" in mermaid
    assert "observed_on" in mermaid
    assert '"' in mermaid  # Quoted labels prevent syntax errors


# ---------------------------------------------------------------------------
# Test 18: Resolution Strictly Requires Verification
# ---------------------------------------------------------------------------

def test_resolution_strictly_requires_verification():
    """Verify UI and evaluation invariants: unverified recovery cannot claim RESOLVED."""
    state = IncidentState(incident_id="inc_test_verif", started_at=utc_now_iso())
    report = IncidentReport(
        incident_id="inc_test_verif",
        started_at=utc_now_iso(),
        ended_at=utc_now_iso(),
        initial_state={},
        evidence_summary=[],
        hypotheses=[],
        root_cause="Payment fault",
        root_cause_evidence=[],
        actions_taken=[],
        verification={},
        final_status="resolved"
    )

    # Missing verification result in state -> MUST NOT display resolved
    assert should_display_resolved(report, state) is False

    # Failed verification in state -> MUST NOT display resolved
    fail_vr = VerificationResult(
        status=VerificationStatus.VERIFICATION_FAILED.value,
        checks=[],
        before_state={},
        after_state={},
        evidence_ids=[],
        summary="Checks failed."
    )
    state.verification_results = [fail_vr]
    assert should_display_resolved(report, state) is False

    # False resolution count function catches this
    assert false_resolution_count([report]) == 1


# ---------------------------------------------------------------------------
# Test 19: No Arbitrary Shell Execution Exposed
# ---------------------------------------------------------------------------

def test_no_arbitrary_shell_execution_exposed():
    """Verify zero arbitrary command execution patterns across all Stage 7 files."""
    stage7_files = [
        Path("agent/evidence_graph.py"),
        Path("evaluation/metrics.py"),
        Path("evaluation/benchmarks.py"),
        Path("evaluation/framework.py")
    ]

    dangerous_patterns = [
        r"shell\s*=\s*True",
        r"os\.system\(",
        r"eval\(",
        r"exec\("
    ]

    for sf in stage7_files:
        assert sf.exists(), f"File {sf} missing!"
        content = sf.read_text(encoding="utf-8")
        for pat in dangerous_patterns:
            matches = re.findall(pat, content)
            assert len(matches) == 0, f"Dangerous pattern '{pat}' found in {sf}!"
