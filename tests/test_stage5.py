import json
import time
from typing import Any, Dict, List
import pytest

from agent.models import (
    ActionStatus,
    ConfidenceLevel,
    Hypothesis,
    HypothesisStatus,
    IncidentPhase,
    IncidentReport,
    IncidentState,
    PlannedAction,
    RiskLevel,
    VerificationStatus,
    utc_now_iso
)
from agent.evidence import EvidenceCorrelator
from agent.hypotheses import HypothesisEngine
from agent.tools import ToolRegistry, ToolDefinition, ToolSecurityError, ALLOWED_SERVICES
from agent.policy import PolicyEngine
from agent.planner import ToolPlanner
from agent.executor import ActionExecutor
from agent.verifier import RecoveryVerifier
from agent.reasoning import DeterministicReasoningBackend
from agent.orchestrator import IncidentOrchestrator
from multimodal.schema import MultimodalEvidenceItem
from sandbox.process_sandbox import ProcessTargetEnvironment
from services.config import GATEWAY_URL, PAYMENT_SERVICE_URL
from telemetry.snapshot import create_incident_snapshot

# ---------------------------------------------------------------------------
# 1. Agent Imports & Interfaces
# ---------------------------------------------------------------------------

def test_agent_imports_correctly():
    """Verify all agent modules, models, and orchestrators import cleanly."""
    reg = ToolRegistry()
    assert len(reg.list_tools()) >= 8

    planner = ToolPlanner()
    assert hasattr(planner, "plan_investigation")
    assert hasattr(planner, "plan_remediation")

    orchestrator = IncidentOrchestrator()
    assert hasattr(orchestrator, "investigate")
    assert hasattr(orchestrator, "run_incident_response")

# ---------------------------------------------------------------------------
# 2. Incident State Data Model
# ---------------------------------------------------------------------------

def test_incident_state_works():
    """Verify IncidentState correctly tracks phases, observations, and transitions."""
    state = IncidentState(incident_id="inc_test_001", started_at=utc_now_iso())
    assert state.phase == IncidentPhase.DETECTION.value
    assert state.final_status == "open"

    state.phase = IncidentPhase.INVESTIGATION.value
    state.observations.append("Observed service degradation")
    assert len(state.observations) == 1

    hyp = Hypothesis(
        id="hyp_001",
        statement="Test hypothesis",
        confidence=ConfidenceLevel.HIGH.value,
        status=HypothesisStatus.SUPPORTED.value
    )
    state.hypotheses.append(hyp)
    state.selected_hypothesis = hyp

    state_dict = state.to_dict()
    assert state_dict["incident_id"] == "inc_test_001"
    assert state_dict["phase"] == IncidentPhase.INVESTIGATION.value
    assert state_dict["selected_hypothesis"]["id"] == "hyp_001"

# ---------------------------------------------------------------------------
# 3. Evidence Correlation & Provenance Preservation
# ---------------------------------------------------------------------------

def test_evidence_correlation_preserves_provenance():
    """Verify EvidenceCorrelator parses telemetry, RAG, and Git without losing provenance."""
    correlator = EvidenceCorrelator()

    snapshot_data = {
        "overall_status": "degraded",
        "timestamp_iso": "2026-09-27T18:00:00Z",
        "service_health": {
            "payment-service": {"status": "degraded", "details": {"fault_active": True}},
            "gateway": {"status": "healthy"}
        },
        "metrics_summary": {
            "gateway": {"error_count": 5, "timeout_count": 5, "status_distribution": {"504": 5}},
            "payment-service": {"error_count": 5, "timeout_count": 5, "status_distribution": {"504": 5}}
        },
        "fault_status": {"active": True, "mode": "timeout", "delay_seconds": 4.5}
    }

    # 1. Telemetry
    tel_items = correlator.correlate_telemetry(snapshot_data, source="test_snapshot.json")
    assert len(tel_items) >= 3
    for item in tel_items:
        assert item.modality == "telemetry"
        assert item.provenance.startswith("health_probe:") or item.provenance.startswith("metrics_summary:") or "fault" in item.provenance
        assert item.status == "verified"
        assert len(item.evidence_id) > 0

    # 2. Knowledge
    know_items = correlator.correlate_knowledge("payment timeout 504", top_k=2)
    assert len(know_items) > 0
    for item in know_items:
        assert item.modality == "text"
        assert "knowledge/runbooks/" in item.provenance or "knowledge/architecture/" in item.provenance

    # 3. Git
    git_items = correlator.correlate_git(["timeout", "payment"], limit=2)
    for item in git_items:
        assert item.modality == "git_diff"
        assert "git_history:" in item.provenance

# ---------------------------------------------------------------------------
# 4. Hypothesis Generation From Real Telemetry
# ---------------------------------------------------------------------------

def test_hypothesis_generation_from_real_telemetry():
    """Verify HypothesisEngine identifies payment timeout hypothesis as supported when evidence warrants it."""
    engine = HypothesisEngine()
    candidates = engine.generate_candidate_hypotheses()
    assert len(candidates) == 4

    # Build supporting evidence for payment timeout
    evidence = [
        MultimodalEvidenceItem(
            evidence_id="ev_fault_active",
            modality="telemetry",
            source="payment-service:/fault/status",
            timestamp=utc_now_iso(),
            content={"active": True, "mode": "timeout", "delay_seconds": 4.5},
            provenance="http://127.0.0.1:8002/fault/status",
            status="verified",
            metadata={"type": "fault_injection_active"}
        ),
        MultimodalEvidenceItem(
            evidence_id="ev_gw_504",
            modality="telemetry",
            source="logs/telemetry.db",
            timestamp=utc_now_iso(),
            content={"service": "gateway", "status_distribution": {504: 8}, "error_count": 8},
            provenance="metrics_summary:gateway@logs/telemetry.db",
            status="verified",
            metadata={"type": "service_metrics"}
        ),
        MultimodalEvidenceItem(
            evidence_id="ev_runbook_payment",
            modality="text",
            source="knowledge/runbooks/payment_timeout.md",
            timestamp=utc_now_iso(),
            content="Payment Service processing delay causes Gateway 504.",
            provenance="knowledge/runbooks/payment_timeout.md#Overview",
            status="verified"
        )
    ]

    evaluated = engine.evaluate_hypotheses(candidates, evidence)
    primary = engine.select_primary_hypothesis(evaluated)

    assert primary is not None
    assert primary.id == HypothesisEngine.HYPOTHESIS_PAYMENT_TIMEOUT
    assert primary.status == HypothesisStatus.SUPPORTED.value
    assert primary.confidence in (ConfidenceLevel.HIGH.value, ConfidenceLevel.MEDIUM.value)
    assert len(primary.supporting_evidence_ids) >= 2

# ---------------------------------------------------------------------------
# 5. Unsupported Hypothesis Is Not Marked Confirmed
# ---------------------------------------------------------------------------

def test_unsupported_hypothesis_is_not_marked_confirmed():
    """Verify that when the system is completely healthy, failure hypotheses are contradicted and never confirmed."""
    engine = HypothesisEngine()
    candidates = engine.generate_candidate_hypotheses()

    # Evidence representing 100% healthy environment
    healthy_evidence = [
        MultimodalEvidenceItem(
            evidence_id="ev_health_all_ok",
            modality="telemetry",
            source="health_probe",
            timestamp=utc_now_iso(),
            content={
                "service_health": {
                    "gateway": {"status": "healthy"},
                    "order-service": {"status": "healthy"},
                    "payment-service": {"status": "healthy", "details": {"fault_active": False}}
                }
            },
            provenance="health_probe:nominal",
            status="verified",
            metadata={"type": "service_health"}
        ),
        MultimodalEvidenceItem(
            evidence_id="ev_metrics_zero_err",
            modality="telemetry",
            source="logs/telemetry.db",
            timestamp=utc_now_iso(),
            content={"service": "gateway", "error_count": 0, "status_distribution": {200: 20}},
            provenance="metrics_summary:gateway",
            status="verified",
            metadata={"type": "service_metrics"}
        )
    ]

    evaluated = engine.evaluate_hypotheses(candidates, healthy_evidence)
    primary = engine.select_primary_hypothesis(evaluated)

    assert primary is not None
    # Nominal operation should be supported, payment timeout MUST NOT be supported
    assert primary.id == HypothesisEngine.HYPOTHESIS_NOMINAL_OPERATION
    assert primary.status == HypothesisStatus.SUPPORTED.value

    payment_hyp = next(h for h in evaluated if h.id == HypothesisEngine.HYPOTHESIS_PAYMENT_TIMEOUT)
    assert payment_hyp.status != HypothesisStatus.SUPPORTED.value
    assert payment_hyp.status in (HypothesisStatus.CONTRADICTED.value, HypothesisStatus.UNVERIFIED.value)

# ---------------------------------------------------------------------------
# 6. Tool Registry Only Exposes Allowlisted Tools
# ---------------------------------------------------------------------------

def test_tool_registry_only_exposes_allowlisted_tools():
    """Verify tool registry contains strictly allowlisted SRE tools with risk classifications."""
    reg = ToolRegistry()
    tools = reg.list_tools()

    expected_tool_names = {
        "inspect_health",
        "inspect_metrics",
        "inspect_recent_logs",
        "inspect_fault_state",
        "retrieve_runbook",
        "retrieve_architecture",
        "inspect_git_changes",
        "probe_traffic",
        "restart_service",
        "reset_controlled_fault"
    }

    actual_names = {t.name for t in tools}
    assert actual_names == expected_tool_names

    for t in tools:
        assert t.risk_level in (RiskLevel.READ.value, RiskLevel.RECOVERY.value, RiskLevel.HIGH.value)
        assert isinstance(t.read_only, bool)
        assert isinstance(t.input_schema, dict)
        assert isinstance(t.output_schema, dict)

# ---------------------------------------------------------------------------
# 7. Arbitrary Command Execution Is Impossible
# ---------------------------------------------------------------------------

def test_arbitrary_command_execution_is_impossible():
    """Verify tool registry strictly forbids executing arbitrary shell or system commands."""
    reg = ToolRegistry()

    with pytest.raises(ToolSecurityError) as excinfo:
        reg.execute("bash", {"command": "rm -rf /"})
    assert "not registered" in str(excinfo.value).lower()

    with pytest.raises(ToolSecurityError):
        reg.execute("powershell", {"command": "Get-Process"})

    with pytest.raises(ToolSecurityError):
        reg.execute("exec", {"code": "import os; os.system('dir')"})

# ---------------------------------------------------------------------------
# 8. Invalid Tool Arguments Are Rejected
# ---------------------------------------------------------------------------

def test_invalid_tool_arguments_are_rejected():
    """Verify tool execution strictly validates argument schemas and service allowlists."""
    reg = ToolRegistry()

    # Unauthorized service target
    with pytest.raises(ToolSecurityError) as excinfo:
        reg.execute("restart_service", {"service": "unauthorized_arbitrary_service"})
    assert "unauthorized" in str(excinfo.value).lower() or "invalid" in str(excinfo.value).lower()

    # Unexpected parameter
    with pytest.raises(ToolSecurityError) as excinfo:
        reg.execute("inspect_health", {"unsupported_param": "malicious_payload"})
    assert "unexpected argument" in str(excinfo.value).lower()

# ---------------------------------------------------------------------------
# 9. Planner Creates Valid Read-Only Investigation Plan
# ---------------------------------------------------------------------------

def test_planner_creates_valid_readonly_investigation_plan():
    """Verify investigation plan consists strictly of non-mutating read actions in logical sequence."""
    planner = ToolPlanner()
    state = IncidentState(incident_id="inc_plan_test", started_at=utc_now_iso())

    plan = planner.plan_investigation(state)
    assert len(plan) >= 4

    tool_names = [a.tool_name for a in plan]
    # Check logical order: health -> metrics -> fault -> runbook
    assert tool_names[0] == "inspect_health"
    assert tool_names[1] == "inspect_metrics"
    assert tool_names[2] == "inspect_fault_state"
    assert tool_names[3] == "retrieve_runbook"

    for action in plan:
        assert action.risk == RiskLevel.READ.value
        assert action.requires_confirmation is False
        assert action.status == ActionStatus.PLANNED.value
        assert len(action.purpose) > 0

# ---------------------------------------------------------------------------
# 10. Recovery Policy Blocks Unsafe Targets
# ---------------------------------------------------------------------------

def test_recovery_policy_blocks_unsafe_targets():
    """Verify PolicyEngine blocks unauthorized restart targets and prevents recovery outside remediation phase."""
    policy = PolicyEngine(permitted_recovery_targets={"gateway", "order-service", "payment-service"})
    reg = ToolRegistry()

    # 1. Unsafe target blocked
    unsafe_action = PlannedAction(
        action_id="act_unsafe",
        tool_name="restart_service",
        arguments={"service": "malicious_daemon"},
        purpose="Try restarting unauthorized target",
        risk=RiskLevel.RECOVERY.value
    )
    state_remediation = IncidentState(
        incident_id="inc_pol_1",
        started_at=utc_now_iso(),
        phase=IncidentPhase.REMEDIATION.value,
        selected_hypothesis=Hypothesis(
            id="hyp_001",
            statement="Test",
            status=HypothesisStatus.SUPPORTED.value
        )
    )
    tool = reg.get_tool("restart_service")
    permitted, reason = policy.evaluate_action(unsafe_action, tool, state_remediation)
    assert permitted is False
    assert "unsafe target" in reason.lower()

    # 2. Recovery action blocked during INVESTIGATION phase
    safe_action = PlannedAction(
        action_id="act_safe",
        tool_name="restart_service",
        arguments={"service": "payment-service"},
        purpose="Restart payment service",
        risk=RiskLevel.RECOVERY.value
    )
    state_investigation = IncidentState(
        incident_id="inc_pol_2",
        started_at=utc_now_iso(),
        phase=IncidentPhase.INVESTIGATION.value
    )
    permitted2, reason2 = policy.evaluate_action(safe_action, tool, state_investigation)
    assert permitted2 is False
    assert "current phase is 'investigation'" in reason2.lower()

# ---------------------------------------------------------------------------
# 11. Action Result Cannot Claim Success After Failed Action
# ---------------------------------------------------------------------------

def test_action_result_cannot_claim_success_after_failed_action():
    """Verify ActionExecutor never reports success if tool execution fails or underlying operation is unsuccessful."""
    reg = ToolRegistry()
    # Replace restart handler with one that returns False
    failing_tool = ToolDefinition(
        name="restart_service",
        description="Mock failing restart",
        input_schema={"service": {"type": "string", "required": True, "allowed": list(ALLOWED_SERVICES)}},
        output_schema={"restarted": "bool"},
        risk_level=RiskLevel.RECOVERY.value,
        read_only=False,
        handler=lambda service: {"service": service, "restarted": False}
    )
    reg.register(failing_tool)

    executor = ActionExecutor(registry=reg, policy=PolicyEngine())
    action = PlannedAction(
        action_id="act_fail_01",
        tool_name="restart_service",
        arguments={"service": "payment-service"},
        purpose="Test failure handling",
        risk=RiskLevel.RECOVERY.value
    )
    state = IncidentState(
        incident_id="inc_fail_test",
        started_at=utc_now_iso(),
        phase=IncidentPhase.REMEDIATION.value,
        selected_hypothesis=Hypothesis(id="h1", statement="Test", status=HypothesisStatus.SUPPORTED.value)
    )

    result = executor.execute_action(action, state)
    assert result.status == ActionStatus.FAILED.value
    assert action.status == ActionStatus.FAILED.value
    assert result.error is not None

# ---------------------------------------------------------------------------
# 12. Verification Detects Recovery
# ---------------------------------------------------------------------------

def test_verification_detects_recovery(monkeypatch):
    """Verify RecoveryVerifier synthesizes verified_recovered when health passes, fault clears, and probe succeeds."""
    verifier = RecoveryVerifier()

    # Mock healthy environment responses
    monkeypatch.setattr(verifier.env, "check_all_health", lambda: {
        "gateway": {"status": "healthy"},
        "order-service": {"status": "healthy"},
        "payment-service": {"status": "healthy"}
    })
    monkeypatch.setattr("agent.verifier.get_fault_status", lambda: {"active": False, "mode": "timeout"})
    monkeypatch.setattr("agent.verifier.generate_traffic", lambda **kwargs: {
        "total_requests": 4,
        "successful_requests": 4,
        "failed_requests": 0,
        "error_rate": 0.0,
        "avg_latency_ms": 150.0
    })

    before_snapshot = {
        "overall_status": "degraded",
        "service_health": {"payment-service": {"status": "degraded"}},
        "fault_status": {"active": True},
        "metrics_summary": {"gateway": {"error_rate": 1.0, "timeout_count": 4}}
    }

    res = verifier.verify_recovery(before_snapshot, wait_seconds=0.0, probe_count=4)
    assert res.status == VerificationStatus.VERIFIED_RECOVERED.value
    assert "complete recovery" in res.summary.lower()
    assert len(res.checks) == 4
    assert all(c["passed"] for c in res.checks)

# ---------------------------------------------------------------------------
# 13. Verification Detects Continued Failure
# ---------------------------------------------------------------------------

def test_verification_detects_continued_failure(monkeypatch):
    """Verify RecoveryVerifier detects still_failing when services remain degraded and traffic fails."""
    verifier = RecoveryVerifier()

    monkeypatch.setattr(verifier.env, "check_all_health", lambda: {
        "gateway": {"status": "healthy"},
        "order-service": {"status": "healthy"},
        "payment-service": {"status": "degraded"}
    })
    monkeypatch.setattr("agent.verifier.get_fault_status", lambda: {"active": True, "mode": "timeout"})
    monkeypatch.setattr("agent.verifier.generate_traffic", lambda **kwargs: {
        "total_requests": 4,
        "successful_requests": 0,
        "failed_requests": 4,
        "error_rate": 1.0,
        "avg_latency_ms": 5000.0
    })

    before_snapshot = {
        "overall_status": "degraded",
        "service_health": {"payment-service": {"status": "degraded"}},
        "fault_status": {"active": True}
    }

    res = verifier.verify_recovery(before_snapshot, wait_seconds=0.0, probe_count=4)
    assert res.status == VerificationStatus.STILL_FAILING.value
    assert res.status != VerificationStatus.VERIFIED_RECOVERED.value
    assert "remain in degraded state" in res.summary.lower()

# ---------------------------------------------------------------------------
# 14. Before/After Telemetry Comparison
# ---------------------------------------------------------------------------

def test_before_after_telemetry_comparison(monkeypatch):
    """Verify verification result preserves accurate empirical before and after states."""
    verifier = RecoveryVerifier()

    monkeypatch.setattr(verifier.env, "check_all_health", lambda: {
        "gateway": {"status": "healthy"},
        "order-service": {"status": "healthy"},
        "payment-service": {"status": "healthy"}
    })
    monkeypatch.setattr("agent.verifier.get_fault_status", lambda: {"active": False})
    monkeypatch.setattr("agent.verifier.generate_traffic", lambda **kwargs: {
        "total_requests": 3,
        "successful_requests": 3,
        "failed_requests": 0,
        "avg_latency_ms": 120.0
    })

    before_snapshot = {
        "overall_status": "degraded",
        "service_health": {"payment-service": {"status": "degraded"}},
        "fault_status": {"active": True},
        "metrics_summary": {"gateway": {"error_rate": 0.75, "timeout_count": 3}}
    }

    res = verifier.verify_recovery(before_snapshot, wait_seconds=0.0, probe_count=3)
    assert res.before_state["overall_status"] == "degraded"
    assert res.before_state["payment_health"] == "degraded"
    assert res.before_state["fault_active"] is True
    assert res.before_state["gateway_error_rate"] == 0.75

    assert res.after_state["overall_status"] == "healthy"
    assert res.after_state["payment_health"] == "healthy"
    assert res.after_state["fault_active"] is False
    assert res.after_state["probe_successful_requests"] == 3
    assert res.after_state["probe_avg_latency_ms"] == 120.0

# ---------------------------------------------------------------------------
# 15. Incident Report Contains Grounded Evidence References
# ---------------------------------------------------------------------------

def test_incident_report_contains_evidence_references():
    """Verify IncidentReport ties root cause to explicit evidence IDs without fabricating citations."""
    orchestrator = IncidentOrchestrator()
    snapshot = {
        "overall_status": "degraded",
        "service_health": {
            "gateway": {"status": "healthy"},
            "order-service": {"status": "healthy"},
            "payment-service": {"status": "degraded", "details": {"fault_active": True}}
        },
        "fault_status": {"active": True, "mode": "timeout", "delay_seconds": 4.5},
        "metrics_summary": {
            "gateway": {"error_count": 4, "timeout_count": 4, "status_distribution": {504: 4}},
            "payment-service": {"error_count": 4, "timeout_count": 4}
        }
    }

    state = orchestrator.investigate(snapshot_data=snapshot)
    report = orchestrator.generate_report(state, snapshot)

    assert isinstance(report, IncidentReport)
    assert len(report.root_cause) > 0
    assert "payment" in report.root_cause.lower()
    # Evidence IDs must be present and match items in evidence_summary
    assert len(report.root_cause_evidence) > 0
    known_eids = {e["id"] for e in report.evidence_summary}
    for cited_id in report.root_cause_evidence:
        assert cited_id in known_eids, f"Cited evidence ID '{cited_id}' not found in evidence summary"

# ---------------------------------------------------------------------------
# 16. Full Real Controlled Incident End-to-End Test
# ---------------------------------------------------------------------------

def test_full_stage5_controlled_incident_flow():
    """Verify full real SRE incident flow: baseline -> inject delay -> traffic -> agent loop -> recovery -> verified."""
    env = ProcessTargetEnvironment()
    orchestrator = IncidentOrchestrator(env=env)

    # 1. Start services and establish clean baseline
    env.start_all()
    time.sleep(1.0)
    health = env.check_all_health()
    assert all(h.get("status") == "healthy" for h in health.values())

    # 2. Inject controlled payment delay fault
    import httpx
    with httpx.Client(timeout=3.0) as client:
        resp = client.post(f"{PAYMENT_SERVICE_URL}/fault/inject", json={
            "mode": "timeout",
            "delay_seconds": 4.0,
            "error_code": 504,
            "description": "Controlled payment-processing timeout fault"
        })
        assert resp.status_code == 200

    # 3. Generate traffic to produce real 504 errors
    from telemetry.traffic import generate_traffic
    traffic_res = generate_traffic(num_requests=3, concurrency=1, interval_seconds=0.03)
    assert traffic_res["failed_requests"] > 0
    assert 504 in traffic_res["status_distribution"]

    # 4. Run full agent incident response loop
    state, report = orchestrator.run_incident_response(execute_recovery=True)

    # 5. Assertions on agent execution
    assert state.phase == IncidentPhase.RESOLVED.value
    assert state.final_status == "resolved"
    assert state.selected_hypothesis is not None
    assert state.selected_hypothesis.id == HypothesisEngine.HYPOTHESIS_PAYMENT_TIMEOUT

    # Recovery action was executed and succeeded
    assert len(state.executed_actions) >= 1
    assert state.executed_actions[0].status == ActionStatus.SUCCEEDED.value

    # Verification confirmed recovery
    assert len(state.verification_results) == 1
    v = state.verification_results[0]
    assert v.status == VerificationStatus.VERIFIED_RECOVERED.value

    # Postmortem report structure
    assert report.final_status == "resolved"
    assert len(report.root_cause_evidence) > 0
