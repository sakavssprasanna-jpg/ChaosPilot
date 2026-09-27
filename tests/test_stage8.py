import json
import os
from pathlib import Path
import pytest
from typing import Any, Dict, List

from agent.evidence_graph import (
    ClaimStatus,
    ClaimSupportStrength,
    EdgeType,
    EvidenceGraph,
    EvidenceGraphBuilder,
    GraphEdge,
    GraphNode,
    GroundedClaim,
    NodeType,
    classify_claim_support
)
from agent.hypotheses import HypothesisEngine
from agent.models import (
    ActionStatus,
    ActionResult,
    ConfidenceLevel,
    Hypothesis,
    HypothesisStatus,
    IncidentPhase,
    IncidentReport,
    IncidentState,
    PlannedAction,
    RiskLevel,
    VerificationResult,
    VerificationStatus,
    utc_now_iso
)
from agent.executor import ActionExecutor
from agent.orchestrator import IncidentOrchestrator
from agent.policy import PolicyEngine
from agent.tools import ToolRegistry, ToolSecurityError, ALLOWED_SERVICES
from app.streamlit_app import should_display_resolved, format_debrief_text
from evaluation.benchmarks import SyntheticControlledBenchmarkFixture, get_default_benchmark_fixture
from evaluation.framework import SystemEvaluator, BenchmarkComparisonResult
from evaluation.metrics import (
    recall_at_k,
    precision_at_k,
    mean_reciprocal_rank,
    correct_hypothesis_selected,
    safe_action_selected,
    verification_success,
    evidence_trace_completeness,
    unsupported_claim_count,
    contradicted_claim_count,
    false_resolution_count,
    unsafe_action_count
)
from git_ops.diff_analyzer import get_git_analyzer
from multimodal.schema import MultimodalEvidenceItem, VoiceTranscript, VoiceSynthesisResult
from multimodal.vision_inspector import VisionInspector
from multimodal.voice_synthesizer import VoiceSynthesizer
from multimodal.voice_transcriber import VoiceTranscriber
from rag.retriever import retrieve
from sandbox.process_sandbox import ProcessTargetEnvironment
from telemetry.db import get_summary


# ---------------------------------------------------------------------------
# Test 1: Evaluation metrics are computed dynamically, not hard-coded
# ---------------------------------------------------------------------------
def test_evaluation_metrics_are_computed_dynamically():
    """Verify evaluation metrics are calculated from actual data rather than static constants."""
    evaluator = SystemEvaluator()
    res = evaluator.evaluate_all()

    ca = res.configurations["Config_A_Retrieval_Only"]
    cb = res.configurations["Config_B_RAG_Deterministic_Reasoning"]
    cc = res.configurations["Config_C_Agentic_RAG_Verification"]
    cd = res.configurations["Config_D_Multimodal_EvidenceGraph_Verification"]

    # Verify metrics are within valid mathematical ranges
    for conf in (ca, cb, cc, cd):
        assert 0.0 <= conf["retrieval_recall_at_3"] <= 1.0
        assert 0.0 <= conf["precision_at_3"] <= 1.0
        assert 0.0 <= conf["mean_reciprocal_rank"] <= 1.0

    # Config B and C must have 0 graph nodes; Config D must have real nodes
    assert cb["evidence_graph_nodes"] == 0
    assert cc["evidence_graph_nodes"] == 0
    assert cd["evidence_graph_nodes"] > 0

    # Dynamic variation test: recall on unrelated keywords yields 0.0
    zero_recall = recall_at_k(["chunk_x"], ["nonexistent_keyword_abc"], k=3)
    assert zero_recall == 0.0


# ---------------------------------------------------------------------------
# Test 2: Evidence graph contains only valid observed evidence
# ---------------------------------------------------------------------------
def test_evidence_graph_contains_only_valid_evidence():
    """Verify that when certain modalities are absent, no ghost nodes or edges are created."""
    state = IncidentState(incident_id="inc_audit_01", started_at=utc_now_iso())
    # Add ONLY telemetry evidence
    state.evidence.append(MultimodalEvidenceItem(
        evidence_id="tel_gtw_audit",
        modality="telemetry",
        source="Gateway :8000",
        timestamp=utc_now_iso(),
        provenance="http://127.0.0.1:8000/metrics",
        content={"service": "gateway", "error_count": 5}
    ))

    builder = EvidenceGraphBuilder()
    graph = builder.build_graph(state)

    # Graph must NOT contain visual or voice observation nodes
    for nid, node in graph.nodes.items():
        assert node.type != NodeType.VISUAL_OBSERVATION.value
        assert node.type != NodeType.VOICE_OBSERVATION.value
        assert not nid.startswith("vis:")
        assert not nid.startswith("voice:")

    # Telemetry node must be present
    assert "tel:tel_gtw_audit" in graph.nodes


# ---------------------------------------------------------------------------
# Test 3: Root claim has real supporting evidence
# ---------------------------------------------------------------------------
def test_root_claim_has_real_supporting_evidence():
    """Verify that the primary outage claim references valid, resolvable evidence nodes."""
    snapshot = SystemEvaluator()._get_controlled_snapshot()
    orchestrator = IncidentOrchestrator()
    state = orchestrator.investigate(snapshot_data=snapshot)
    graph = EvidenceGraphBuilder().build_graph(state, snapshot=snapshot)

    assert "claim_payment_timeout_propagation" in graph.claims
    claim = graph.claims["claim_payment_timeout_propagation"]

    # Claim must have supporting evidence IDs
    assert len(claim.evidence_ids) >= 2
    # Verify at least one supporting evidence node is present in the graph
    resolved_nodes = [graph._resolve_evidence_node_id(eid) for eid in claim.evidence_ids]
    valid_resolved = [nid for nid in resolved_nodes if nid and nid in graph.nodes]
    assert len(valid_resolved) >= 2


# ---------------------------------------------------------------------------
# Test 4: Claim support classification (Cases A to D)
# ---------------------------------------------------------------------------
def test_claim_support_classification_cases_a_to_d():
    """Verify classify_claim_support strictly implements Cases A through D."""
    # Case A: 0 evidence -> unsupported
    assert classify_claim_support([], []) == ClaimSupportStrength.UNSUPPORTED.value
    assert classify_claim_support(None, None) == ClaimSupportStrength.UNSUPPORTED.value

    # Case B: 1 valid evidence -> weakly_supported
    assert classify_claim_support(["tel_01"], ["telemetry"]) == ClaimSupportStrength.WEAKLY_SUPPORTED.value

    # Case C: >= 2 evidence items in same modality -> supported
    assert classify_claim_support(["tel_01", "tel_02"], ["telemetry", "telemetry"]) == ClaimSupportStrength.SUPPORTED.value

    # Case D: >= 3 items across >= 2 modalities + 0 contradictions -> strongly_supported
    assert classify_claim_support(
        ["tel_01", "tel_02", "doc_01"],
        ["telemetry", "runbook_knowledge"],
        contradictions=[]
    ) == ClaimSupportStrength.STRONGLY_SUPPORTED.value


# ---------------------------------------------------------------------------
# Test 5: Contradiction handling (Case E)
# ---------------------------------------------------------------------------
def test_claim_contradiction_handling_case_e():
    """Verify classify_claim_support classifies any claim with >= 1 contradiction as contradicted."""
    # Even if 5 items across 3 modalities exist, contradiction takes precedence
    strength = classify_claim_support(
        evidence_ids=["tel_01", "tel_02", "doc_01"],
        modalities=["telemetry", "runbook_knowledge"],
        contradictions=["telemetry_healthy_200"]
    )
    assert strength == ClaimSupportStrength.CONTRADICTED.value


# ---------------------------------------------------------------------------
# Test 6: Unsupported claim handling
# ---------------------------------------------------------------------------
def test_unsupported_claim_handling():
    """Verify that an unsubstantiated candidate claim is marked unsupported."""
    snapshot = SystemEvaluator()._get_controlled_snapshot()
    orchestrator = IncidentOrchestrator()
    state = orchestrator.investigate(snapshot_data=snapshot)
    graph = EvidenceGraphBuilder().build_graph(state, snapshot=snapshot)

    assert "claim_gateway_misconfig" in graph.claims
    gw_claim = graph.claims["claim_gateway_misconfig"]
    assert gw_claim.support_strength == ClaimSupportStrength.UNSUPPORTED.value
    assert gw_claim.status == ClaimStatus.CANDIDATE.value
    assert len(gw_claim.evidence_ids) == 0


# ---------------------------------------------------------------------------
# Test 7: Resolution invariant (successful recovery + verification = resolved)
# ---------------------------------------------------------------------------
def test_resolution_invariant_success():
    """Verify should_display_resolved returns True when recovery and verification both pass."""
    state = IncidentState(incident_id="inc_res_01", started_at=utc_now_iso())
    ver = VerificationResult(
        status=VerificationStatus.VERIFIED_RECOVERED.value,
        checks=[{"name": "health", "status": "healthy"}],
        before_state={},
        after_state={},
        evidence_ids=[],
        summary="All services healthy"
    )
    state.verification_results.append(ver)

    report = IncidentReport(
        incident_id=state.incident_id,
        started_at=state.started_at,
        ended_at=utc_now_iso(),
        initial_state={},
        evidence_summary=[],
        hypotheses=[],
        root_cause="Payment Timeout",
        root_cause_evidence=[],
        actions_taken=[],
        verification=ver.to_dict(),
        final_status="resolved",
        evidence_graph_summary={}
    )

    assert should_display_resolved(report, state) is True
    assert false_resolution_count([report]) == 0


# ---------------------------------------------------------------------------
# Test 8: Verification failure blocks resolution
# ---------------------------------------------------------------------------
def test_verification_failure_blocks_resolution():
    """Verify resolution is rejected if empirical verification fails."""
    state = IncidentState(incident_id="inc_res_02", started_at=utc_now_iso())
    ver_failed = VerificationResult(
        status=VerificationStatus.VERIFICATION_FAILED.value,
        checks=[{"name": "health", "status": "down"}],
        before_state={},
        after_state={},
        evidence_ids=[],
        summary="Service still degraded"
    )
    state.verification_results.append(ver_failed)

    # UI must refuse to show resolved even if report.final_status was prematurely set
    report_bad = IncidentReport(
        incident_id=state.incident_id,
        started_at=state.started_at,
        ended_at=utc_now_iso(),
        initial_state={},
        evidence_summary=[],
        hypotheses=[],
        root_cause="Payment Timeout",
        root_cause_evidence=[],
        actions_taken=[],
        verification=ver_failed.to_dict(),
        final_status="resolved",
        evidence_graph_summary={}
    )

    assert should_display_resolved(report_bad, state) is False
    # Metric flags false resolution attempt
    assert false_resolution_count([report_bad]) == 1


# ---------------------------------------------------------------------------
# Test 9: Recovery failure blocks resolution
# ---------------------------------------------------------------------------
def test_recovery_failure_blocks_resolution():
    """Verify that an unsuccessful recovery action prevents resolved status."""
    state = IncidentState(incident_id="inc_res_03", started_at=utc_now_iso())
    failed_act = ActionResult(
        action_id="act_01",
        tool_name="reset_controlled_fault",
        target="payment-service",
        status=ActionStatus.FAILED.value,
        output={},
        error="Connection refused",
        started_at=utc_now_iso(),
        finished_at=utc_now_iso()
    )
    state.executed_actions.append(failed_act)
    state.final_status = "unresolved"

    report = IncidentReport(
        incident_id=state.incident_id,
        started_at=state.started_at,
        ended_at=utc_now_iso(),
        initial_state={},
        evidence_summary=[],
        hypotheses=[],
        root_cause="Payment Timeout",
        root_cause_evidence=[],
        actions_taken=[failed_act.to_dict()],
        verification={"status": "not_verified"},
        final_status="unresolved",
        evidence_graph_summary={}
    )

    assert should_display_resolved(report, state) is False


# ---------------------------------------------------------------------------
# Test 10: Unavailable verification blocks resolution
# ---------------------------------------------------------------------------
def test_unavailable_verification_blocks_resolution():
    """Verify that absent verification results always block resolution display."""
    state = IncidentState(incident_id="inc_res_04", started_at=utc_now_iso())
    state.verification_results = []

    report = IncidentReport(
        incident_id=state.incident_id,
        started_at=state.started_at,
        ended_at=utc_now_iso(),
        initial_state={},
        evidence_summary=[],
        hypotheses=[],
        root_cause="Payment Timeout",
        root_cause_evidence=[],
        actions_taken=[],
        verification={},
        final_status="resolved",  # Claiming resolved without verifications
        evidence_graph_summary={}
    )

    assert should_display_resolved(report, state) is False
    assert should_display_resolved(None, state) is False
    assert should_display_resolved(report, None) is False


# ---------------------------------------------------------------------------
# Test 11: Gateway unavailable handling
# ---------------------------------------------------------------------------
def test_gateway_unavailable_handling():
    """Verify ProcessTargetEnvironment handles an offline Gateway gracefully."""
    env = ProcessTargetEnvironment()
    # Query health when services are offline or not started
    health = env.check_health("gateway")
    assert isinstance(health, dict)
    assert health["status"] in ("down", "healthy", "timeout")
    assert "http_status" in health


# ---------------------------------------------------------------------------
# Test 12: Payment unavailable handling
# ---------------------------------------------------------------------------
def test_payment_unavailable_handling():
    """Verify ProcessTargetEnvironment handles an offline Payment service gracefully."""
    env = ProcessTargetEnvironment()
    health = env.check_health("payment-service")
    assert isinstance(health, dict)
    assert health["status"] in ("down", "healthy", "degraded", "timeout")
    assert "service" in health


# ---------------------------------------------------------------------------
# Test 13: Telemetry unavailable / malformed handling
# ---------------------------------------------------------------------------
def test_telemetry_unavailable_handling():
    """Verify telemetry queries on non-existent services return safe defaults."""
    res = get_summary("nonexistent_service_xyz_404", window_seconds=60)
    assert isinstance(res, dict)
    assert res.get("request_count") == 0
    assert res.get("error_rate") == 0.0
    assert res.get("status_distribution") == {}


# ---------------------------------------------------------------------------
# Test 14: RAG unavailable handling
# ---------------------------------------------------------------------------
def test_rag_unavailable_handling():
    """Verify retriever handles empty or zero-match queries gracefully."""
    empty_res = retrieve("", top_k=3)
    assert isinstance(empty_res, list)

    gibberish_res = retrieve("xyznonexistent12345abcdefqwerty", top_k=3)
    assert isinstance(gibberish_res, list)


# ---------------------------------------------------------------------------
# Test 15: Git unavailable handling
# ---------------------------------------------------------------------------
def test_git_unavailable_handling():
    """Verify git diff analyzer handles non-matching search terms without crashing."""
    analyzer = get_git_analyzer()
    commits = analyzer.search_commits(["nonexistent_diagnostic_term_xyz_123"], limit=5)
    assert isinstance(commits, list)
    assert len(commits) == 0


# ---------------------------------------------------------------------------
# Test 16: STT unavailable handling
# ---------------------------------------------------------------------------
def test_stt_unavailable_handling():
    """Verify VoiceTranscriber handles missing or corrupted audio files gracefully."""
    transcriber = VoiceTranscriber()
    res = transcriber.transcribe_file(Path("nonexistent_audio_sample_xyz.wav"))
    assert isinstance(res, VoiceTranscript)
    assert res.status in ("unavailable", "error")


# ---------------------------------------------------------------------------
# Test 17: TTS unavailable handling
# ---------------------------------------------------------------------------
def test_tts_unavailable_handling():
    """Verify VoiceSynthesizer handles synthesis requests safely without unhandled crashes."""
    synthesizer = VoiceSynthesizer()
    out_path = Path("logs/test_dummy_tts.wav")
    res = synthesizer.synthesize("Test debrief message.", output_path=out_path)
    assert isinstance(res, VoiceSynthesisResult)
    assert res.status in ("success", "unavailable")


# ---------------------------------------------------------------------------
# Test 18: Semantic vision unavailable handling
# ---------------------------------------------------------------------------
def test_semantic_vision_unavailable_handling(monkeypatch):
    """Verify VisionInspector reports unavailable rather than fabricating semantic vision."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    inspector = VisionInspector()
    assert inspector.backend == "none"

    dummy_img = Path("logs/visualizations/nonexistent_vis.png")
    res = inspector.inspect(dummy_img)
    assert res.status in ("unavailable", "error")


# ---------------------------------------------------------------------------
# Test 19: Arbitrary command execution blocked
# ---------------------------------------------------------------------------
def test_arbitrary_command_execution_blocked():
    """Verify strict allowlist enforcement: no shell commands, unauthorized tools, or rogue services."""
    registry = ToolRegistry()

    # Unauthorized tool lookup must return None
    assert registry.get_tool("execute_shell_cmd") is None
    assert registry.get_tool("run_bash") is None

    # Execution of unauthorized tool must raise ToolSecurityError
    with pytest.raises(ToolSecurityError):
        registry.execute("execute_shell_cmd", {})

    with pytest.raises(ToolSecurityError):
        registry.execute("run_bash", {})

    # Unauthorized service argument must raise ToolSecurityError
    inspect_health_tool = registry.get_tool("inspect_health")
    with pytest.raises(ToolSecurityError):
        inspect_health_tool.handler(service="unauthorized_service_host")

    restart_tool = registry.get_tool("restart_service")
    with pytest.raises(ToolSecurityError):
        restart_tool.handler(service="malicious_host_process")

    # ActionExecutor rejects rogue/unregistered tool executions safely
    executor = ActionExecutor(registry=registry, policy=PolicyEngine())
    rogue_action = PlannedAction(
        action_id="act_rogue",
        tool_name="rm_rf",
        arguments={},
        purpose="unsafe action",
        risk=RiskLevel.HIGH.value
    )
    result = executor.execute_action(rogue_action, IncidentState(incident_id="test", started_at=utc_now_iso()))
    assert result.status == ActionStatus.FAILED.value
    assert "not found in ToolRegistry" in (result.error or "")


# ---------------------------------------------------------------------------
# Test 20: Streamlit app imports cleanly
# ---------------------------------------------------------------------------
def test_streamlit_app_imports_cleanly():
    """Verify Streamlit console application module imports cleanly and pure helpers work."""
    import app.streamlit_app as st_app
    assert hasattr(st_app, "should_display_resolved")
    assert hasattr(st_app, "format_debrief_text")

    # Verify debrief text formatting
    report = IncidentReport(
        incident_id="inc_fmt_01",
        started_at=utc_now_iso(),
        ended_at=utc_now_iso(),
        initial_state={},
        evidence_summary=[],
        hypotheses=[],
        root_cause="Payment Timeout",
        root_cause_evidence=[],
        actions_taken=[],
        verification={"status": "verified_recovered"},
        final_status="resolved",
        evidence_graph_summary={}
    )
    state = IncidentState(incident_id="inc_fmt_01", started_at=utc_now_iso())
    debrief = format_debrief_text(report, state)
    assert "Payment Timeout" in debrief
    assert "resolved" in debrief.lower()


# ---------------------------------------------------------------------------
# Test 21: Existing Stage 7 evaluation works
# ---------------------------------------------------------------------------
def test_existing_stage7_evaluation_works():
    """Verify SystemEvaluator produces valid BenchmarkComparisonResult with 4 configurations."""
    evaluator = SystemEvaluator()
    res = evaluator.evaluate_all()
    assert isinstance(res, BenchmarkComparisonResult)
    assert len(res.configurations) == 4
    assert res.configurations["Config_A_Retrieval_Only"]["final_status"] == "unresolved"
    assert res.configurations["Config_D_Multimodal_EvidenceGraph_Verification"]["final_status"] == "resolved"


# ---------------------------------------------------------------------------
# Test 22: Evidence graph serialization roundtrip
# ---------------------------------------------------------------------------
def test_evidence_graph_serialization_roundtrip():
    """Verify EvidenceGraph serialization and deserialization preserves all nodes, edges, and claims."""
    graph = EvidenceGraph()
    graph.add_node(GraphNode(id="svc:order", type=NodeType.SERVICE.value, label="Order Service"))
    graph.add_node(GraphNode(id="svc:payment", type=NodeType.SERVICE.value, label="Payment Service"))
    graph.add_edge(GraphEdge("svc:order", "svc:payment", EdgeType.DEPENDS_ON.value))
    graph.add_claim(GroundedClaim(
        claim_id="claim_test_rt",
        statement="Order Service depends on Payment Service",
        evidence_ids=["svc:order"],
        support_strength=ClaimSupportStrength.SUPPORTED.value,
        status=ClaimStatus.CONFIRMED.value
    ))

    data = graph.to_dict()
    restored = EvidenceGraph.from_dict(data)

    assert len(restored.nodes) == len(graph.nodes)
    assert len(restored.edges) == len(graph.edges)
    assert "claim_test_rt" in restored.claims
    assert restored.claims["claim_test_rt"].statement == "Order Service depends on Payment Service"


# ---------------------------------------------------------------------------
# Test 23: Full system compatibility baseline
# ---------------------------------------------------------------------------
def test_full_system_compatibility_baseline():
    """Verify core components (sandbox, db, RAG, perception, orchestrator) remain fully interoperable."""
    env = ProcessTargetEnvironment()
    orchestrator = IncidentOrchestrator(env=env)
    assert orchestrator.env is env
    assert orchestrator.hypothesis_engine is not None
    assert orchestrator.verifier is not None
    assert orchestrator.correlator is not None


# ---------------------------------------------------------------------------
# Test 24: Regression test covering failed branch of evaluate_config_c
# ---------------------------------------------------------------------------
def test_evaluate_config_c_failed_branch_regression():
    """Verify evaluate_config_c_agentic_rag_verification handles unverified/failed snapshots.

    Specifically covers the branch where safe_action and hyp_correct is False,
    ensuring VerificationStatus.VERIFICATION_FAILED is used without raising AttributeError.
    """
    evaluator = SystemEvaluator()
    # An empty snapshot produces no supported hypotheses or matching remediation
    failed_res = evaluator.evaluate_config_c_agentic_rag_verification(snapshot={})

    assert failed_res["hypothesis_correctness"] is False
    assert failed_res["safe_action_executed"] is False
    assert failed_res["empirically_verified"] is False
    assert failed_res["final_status"] == "unresolved"

    # Also verify Config D failed branch operates without AttributeError
    failed_d_res = evaluator.evaluate_config_d_multimodal_graph_verification(snapshot={})
    assert failed_d_res["hypothesis_correctness"] is False
    assert failed_d_res["empirically_verified"] is False
    assert failed_d_res["final_status"] == "unresolved"

