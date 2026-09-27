import io
import json
from pathlib import Path
import re
import time
from typing import Any, Dict, List
import pytest

from agent.models import (
    ActionResult,
    ActionStatus,
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
from agent.orchestrator import IncidentOrchestrator
from agent.tools import ToolRegistry, ALLOWED_SERVICES
from app.streamlit_app import (
    should_display_resolved,
    format_debrief_text,
    get_multimodal_evidence_summary,
    extract_before_after_comparison
)
from manage import cmd_console
from multimodal.schema import MultimodalEvidenceItem
from multimodal.vision_inspector import VisionInspector
from multimodal.visualizer import generate_topology_diagram, generate_telemetry_chart
from multimodal.voice_synthesizer import VoiceSynthesizer
from multimodal.voice_transcriber import VoiceTranscriber
from sandbox.process_sandbox import ProcessTargetEnvironment
from services.config import LOGS_DIR
from telemetry.db import get_all_summaries


# ---------------------------------------------------------------------------
# Test 1: Operator console imports cleanly
# ---------------------------------------------------------------------------

def test_operator_console_imports_cleanly():
    """Verify that the console application and helper modules import without error."""
    import app.streamlit_app as st_app
    assert hasattr(st_app, "main")
    assert hasattr(st_app, "should_display_resolved")
    assert hasattr(st_app, "format_debrief_text")
    assert hasattr(st_app, "get_multimodal_evidence_summary")
    assert hasattr(st_app, "extract_before_after_comparison")
    assert callable(cmd_console)


# ---------------------------------------------------------------------------
# Test 2: Live status collection uses real target environment state
# ---------------------------------------------------------------------------

def test_status_collection_uses_real_service_state():
    """Verify health and telemetry queries return genuine target state dictionaries."""
    env = ProcessTargetEnvironment()
    health = env.check_all_health()

    assert "gateway" in health
    assert "order-service" in health
    assert "payment-service" in health

    for svc_name, h_info in health.items():
        assert "status" in h_info
        assert h_info["status"] in ("healthy", "degraded", "stopped", "down", "timeout", "unhealthy", "unreachable", "unknown")
        assert "http_status" in h_info

    summaries = get_all_summaries(window_seconds=300.0)
    assert isinstance(summaries, dict)
    assert "gateway" in summaries
    assert "order-service" in summaries
    assert "payment-service" in summaries
    assert "request_count" in summaries["gateway"]
    assert "error_count" in summaries["gateway"]


# ---------------------------------------------------------------------------
# Test 3: Operator directive text/voice input passed to existing orchestrator
# ---------------------------------------------------------------------------

def test_operator_directive_passed_to_orchestrator():
    """Verify operator directive is recorded in state, observations, and timeline events."""
    orchestrator = IncidentOrchestrator()
    directive = "Investigate payment service 504 degradation and restore nominal traffic."

    dummy_snapshot = {
        "timestamp_iso": utc_now_iso(),
        "overall_status": "degraded",
        "service_health": {
            "gateway": {"status": "healthy", "http_status": 200},
            "order-service": {"status": "healthy", "http_status": 200},
            "payment-service": {"status": "degraded", "http_status": 200, "details": {"fault_mode": "timeout"}}
        },
        "telemetry_summary": {
            "gateway": {"request_count": 10, "error_count": 4, "error_rate": 0.4, "status_distribution": {"504": 4, "200": 6}}
        },
        "fault_status": {"active": True, "mode": "timeout", "delay_seconds": 4.5}
    }

    state = orchestrator.investigate(snapshot_data=dummy_snapshot, directive=directive)

    assert state.directive == directive
    assert any(directive in obs for obs in state.observations)
    assert any(ev.get("stage") == "directive" and ev.get("details", {}).get("directive") == directive for ev in state.events)


# ---------------------------------------------------------------------------
# Test 4: Voice transcript is not fabricated
# ---------------------------------------------------------------------------

def test_voice_transcript_is_not_fabricated():
    """Verify speech transcriber never invents text for invalid or corrupted audio."""
    transcriber = VoiceTranscriber()
    # Garbage bytes
    res_corrupt = transcriber.transcribe_bytes(b"corrupt_noise_payload_0123456789", audio_format="wav")
    assert res_corrupt.status == "error"
    assert res_corrupt.transcript is None or res_corrupt.transcript == ""

    # Empty payload
    res_empty = transcriber.transcribe_bytes(b"", audio_format="wav")
    assert res_empty.status == "error"
    assert res_empty.transcript is None or res_empty.transcript == ""


# ---------------------------------------------------------------------------
# Test 5: Agent event timeline receives real events from IncidentOrchestrator
# ---------------------------------------------------------------------------

def test_agent_event_timeline_receives_real_events():
    """Verify live timeline callback captures structured lifecycle events."""
    captured_events: List[Dict[str, Any]] = []

    def on_event(ev: Dict[str, Any]):
        captured_events.append(ev)

    orchestrator = IncidentOrchestrator(event_callback=on_event)

    dummy_snapshot = {
        "timestamp_iso": utc_now_iso(),
        "overall_status": "nominal",
        "service_health": {
            "gateway": {"status": "healthy", "http_status": 200},
            "order-service": {"status": "healthy", "http_status": 200},
            "payment-service": {"status": "healthy", "http_status": 200}
        },
        "telemetry_summary": {},
        "fault_status": {"active": False}
    }

    state = orchestrator.investigate(snapshot_data=dummy_snapshot)

    assert len(captured_events) >= 5
    assert len(state.events) == len(captured_events)

    stages = [ev["stage"] for ev in captured_events]
    assert "detection" in stages
    assert "investigation" in stages
    assert "telemetry" in stages
    assert "rag" in stages
    assert "hypotheses" in stages

    for ev in captured_events:
        assert "timestamp" in ev
        assert "stage" in ev
        assert "message" in ev
        assert "details" in ev


# ---------------------------------------------------------------------------
# Test 6: Multimodal evidence tabs preserve evidence provenance
# ---------------------------------------------------------------------------

def test_multimodal_evidence_tabs_preserve_provenance():
    """Verify all evidence items preserve source, provenance, and modality metadata."""
    orchestrator = IncidentOrchestrator()

    dummy_snapshot = {
        "timestamp_iso": utc_now_iso(),
        "overall_status": "degraded",
        "service_health": {
            "gateway": {"status": "healthy", "http_status": 200},
            "order-service": {"status": "healthy", "http_status": 200},
            "payment-service": {"status": "degraded", "http_status": 200, "details": {"fault_mode": "timeout"}}
        },
        "telemetry_summary": {
            "gateway": {"request_count": 8, "error_count": 3, "error_rate": 0.375, "status_distribution": {"504": 3, "200": 5}}
        },
        "fault_status": {"active": True, "mode": "timeout", "delay_seconds": 4.5}
    }

    state = orchestrator.investigate(snapshot_data=dummy_snapshot)
    assert len(state.evidence) > 0

    summary = get_multimodal_evidence_summary(state)
    assert summary["telemetry"] > 0
    assert summary["text"] > 0

    for item in state.evidence:
        assert hasattr(item, "evidence_id") and item.evidence_id
        assert hasattr(item, "provenance") and item.provenance
        assert hasattr(item, "source") and item.source
        assert hasattr(item, "modality") and item.modality in (
            "telemetry", "text", "git_diff", "visual_observation", "voice_transcript"
        )


# ---------------------------------------------------------------------------
# Test 7: Root cause panel displays real IncidentReport root cause & citations
# ---------------------------------------------------------------------------

def test_root_cause_panel_displays_real_report():
    """Verify root cause and citations in report correspond to empirical evidence."""
    orchestrator = IncidentOrchestrator()

    dummy_snapshot = {
        "timestamp_iso": utc_now_iso(),
        "overall_status": "degraded",
        "service_health": {
            "gateway": {"status": "healthy", "http_status": 200},
            "order-service": {"status": "healthy", "http_status": 200},
            "payment-service": {"status": "degraded", "http_status": 200, "details": {"fault_mode": "timeout"}}
        },
        "telemetry_summary": {
            "gateway": {"request_count": 10, "error_count": 5, "error_rate": 0.5, "status_distribution": {"504": 5}}
        },
        "fault_status": {"active": True, "mode": "timeout", "delay_seconds": 4.5}
    }

    state = orchestrator.investigate(snapshot_data=dummy_snapshot)
    report = orchestrator.generate_report(state, dummy_snapshot)

    assert isinstance(report, IncidentReport)
    assert report.incident_id == state.incident_id
    assert report.root_cause
    assert "timeout" in report.root_cause.lower() or "payment" in report.root_cause.lower()
    assert isinstance(report.root_cause_evidence, list)
    assert len(report.root_cause_evidence) > 0

    # Ensure all cited evidence IDs exist in state
    state_eids = {e.evidence_id for e in state.evidence}
    for cid in report.root_cause_evidence:
        assert cid in state_eids


# ---------------------------------------------------------------------------
# Test 8: Recovery panel displays real ActionResult and audit records
# ---------------------------------------------------------------------------

def test_recovery_panel_displays_real_action_results():
    """Verify ActionResult data structures conform to audit and execution requirements."""
    ar = ActionResult(
        action_id="act_test_01",
        tool_name="reset_payment_fault",
        target="payment-service",
        started_at=utc_now_iso(),
        finished_at=utc_now_iso(),
        status=ActionStatus.SUCCEEDED.value,
        output={"status": "success", "message": "Fault reset successfully"},
        error=None,
        evidence_ids=["ev_1", "ev_2"]
    )

    d = ar.to_dict()
    assert d["action_id"] == "act_test_01"
    assert d["tool_name"] == "reset_payment_fault"
    assert d["status"] == "succeeded"
    assert d["output"]["status"] == "success"
    assert d["evidence_ids"] == ["ev_1", "ev_2"]


# ---------------------------------------------------------------------------
# Test 9: Verification panel displays real VerificationResult with before/after
# ---------------------------------------------------------------------------

def test_verification_panel_displays_real_verification():
    """Verify VerificationResult contains before/after snapshots and probe checks."""
    vr = VerificationResult(
        status=VerificationStatus.VERIFIED_RECOVERED.value,
        checks=[
            {"check": "payment_health", "passed": True, "details": "Healthy HTTP 200"},
            {"check": "probe_traffic", "passed": True, "details": "4/4 probes succeeded"}
        ],
        before_state={"overall_status": "degraded", "fault_active": True, "gateway_error_rate": 0.5},
        after_state={"overall_status": "nominal", "fault_active": False, "probe_successful_requests": 4},
        evidence_ids=["ev_tel_01"],
        summary="All 2 recovery verification checks passed successfully."
    )

    b_s, a_s = extract_before_after_comparison(vr)
    assert b_s["overall_status"] == "degraded"
    assert b_s["fault_active"] is True
    assert a_s["overall_status"] == "nominal"
    assert a_s["fault_active"] is False

    d = vr.to_dict()
    assert d["status"] == "verified_recovered"
    assert len(d["checks"]) == 2


# ---------------------------------------------------------------------------
# Test 10: Console does not display "resolved" if verification failed
# ---------------------------------------------------------------------------

def test_console_never_displays_resolved_if_verification_failed():
    """SRE INVARIANT: The UI must reject 'RESOLVED' display if verification failed or is absent."""
    state = IncidentState(incident_id="inc_001", started_at=utc_now_iso())
    report = IncidentReport(
        incident_id="inc_001",
        started_at=utc_now_iso(),
        ended_at=utc_now_iso(),
        initial_state={},
        evidence_summary=[],
        hypotheses=[],
        final_status="resolved",  # Claimed resolved
        root_cause="Payment fault",
        root_cause_evidence=[],
        actions_taken=[],
        verification={}
    )

    # 1. Claimed resolved, but NO verification results in state -> MUST NOT display resolved
    assert should_display_resolved(report, state) is False

    # 2. Claimed resolved, but verification failed -> MUST NOT display resolved
    fail_vr = VerificationResult(
        status=VerificationStatus.VERIFICATION_FAILED.value,
        checks=[{"check": "probe_traffic", "passed": False}],
        before_state={},
        after_state={},
        evidence_ids=[],
        summary="Probe traffic failed."
    )
    state.verification_results.append(fail_vr)
    assert should_display_resolved(report, state) is False

    # 3. Report says unresolved, but verification succeeded -> MUST NOT display resolved
    report.final_status = "unresolved"
    success_vr = VerificationResult(
        status=VerificationStatus.VERIFIED_RECOVERED.value,
        checks=[{"check": "probe_traffic", "passed": True}],
        before_state={},
        after_state={},
        evidence_ids=[],
        summary="All probes succeeded."
    )
    state.verification_results = [success_vr]
    assert should_display_resolved(report, state) is False

    # 4. Report is resolved AND verification is VERIFIED_RECOVERED -> MUST display resolved
    report.final_status = "resolved"
    assert should_display_resolved(report, state) is True


# ---------------------------------------------------------------------------
# Test 11: Voice debrief failure is handled gracefully without crashing
# ---------------------------------------------------------------------------

def test_voice_debrief_failure_handled_gracefully():
    """Verify voice synthesizer surfaces errors without throwing unhandled exceptions."""
    synth = VoiceSynthesizer()

    # Empty text fails gracefully
    res_empty = synth.synthesize("")
    assert res_empty.status == "error"
    assert res_empty.error is not None

    # Debrief text formatting works reliably
    report = IncidentReport(
        incident_id="inc_test_1234567890",
        started_at=utc_now_iso(),
        ended_at=utc_now_iso(),
        initial_state={},
        evidence_summary=[],
        hypotheses=[],
        final_status="resolved",
        root_cause="Payment service upstream timeout due to injected fault.",
        root_cause_evidence=[],
        actions_taken=[],
        verification={}
    )
    state = IncidentState(incident_id="inc_test_1234567890", started_at=utc_now_iso())
    script = format_debrief_text(report, state)
    assert "inc_test_123" in script
    assert "resolved" in script
    assert "Payment service upstream timeout" in script


# ---------------------------------------------------------------------------
# Test 12: Vision unavailable state is displayed honestly
# ---------------------------------------------------------------------------

def test_vision_unavailable_state_displayed_honestly():
    """Verify VisionInspector reports structural data honestly when Gemini is unconfigured."""
    top_path = LOGS_DIR / "visualizations" / "test_topology.png"
    top_path.parent.mkdir(parents=True, exist_ok=True)
    generate_topology_diagram(top_path)

    # Force backend='none' or local structural
    inspector = VisionInspector(backend="none")
    res = inspector.inspect(top_path)

    assert res.backend in ("none", "local_structural")
    assert "dimensions" in res.metadata
    assert "format" in res.metadata
    assert res.metadata["format"].upper() == "PNG"
    # Does not hallucinate an AI model output
    assert "Gemini vision model is not configured" in (res.error or "") or res.backend == "none"


# ---------------------------------------------------------------------------
# Test 13: No arbitrary command execution exposed via UI inputs
# ---------------------------------------------------------------------------

def test_no_arbitrary_shell_execution_exposed():
    """Verify zero arbitrary shell execution patterns exist across console and agent."""
    code_files = [
        Path("app/streamlit_app.py"),
        Path("agent/tools.py"),
        Path("agent/executor.py"),
        Path("agent/orchestrator.py")
    ]

    dangerous_patterns = [
        r"shell\s*=\s*True",
        r"os\.system\(",
        r"eval\(",
        r"exec\("
    ]

    for cf in code_files:
        if not cf.exists():
            continue
        content = cf.read_text(encoding="utf-8")
        for pat in dangerous_patterns:
            matches = re.findall(pat, content)
            assert len(matches) == 0, f"Dangerous pattern '{pat}' found in {cf}!"

    # Verify tool registry allows only allowlisted operations
    reg = ToolRegistry()
    tools = reg.list_tools()
    assert len(tools) >= 8
    for tdef in tools:
        assert tdef.risk_level in ("read", "recovery", "high")
        assert tdef.handler is not None
        assert callable(tdef.handler)


# ---------------------------------------------------------------------------
# Test 14: All Stage 1-5 capabilities continue to work cleanly
# ---------------------------------------------------------------------------

def test_all_previous_stages_remain_compatible():
    """Verify core interfaces from Stages 1-5 integrate without regression."""
    # Stage 1 sandbox
    env = ProcessTargetEnvironment()
    assert hasattr(env, "start_all")
    assert hasattr(env, "stop_all")

    # Stage 2 telemetry
    assert hasattr(env, "check_all_health")

    # Stage 3 RAG & Git
    from rag.retriever import retrieve
    citations = retrieve("timeout fault payment", top_k=2)
    assert len(citations) > 0

    # Stage 4 Multimodal
    top_path = LOGS_DIR / "visualizations" / "test_stage6_topology.png"
    out_path = generate_topology_diagram(top_path)
    assert Path(out_path).exists()

    # Stage 5 Agent
    orchestrator = IncidentOrchestrator(env=env)
    assert hasattr(orchestrator, "investigate")
    assert hasattr(orchestrator, "run_incident_response")
