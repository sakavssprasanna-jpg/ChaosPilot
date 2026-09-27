from datetime import datetime, timezone
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from agent.evidence import EvidenceCorrelator
from agent.executor import ActionExecutor
from agent.hypotheses import HypothesisEngine
from agent.models import (
    ActionStatus,
    ConfidenceLevel,
    Hypothesis,
    HypothesisStatus,
    IncidentPhase,
    IncidentReport,
    IncidentState,
    PlannedAction,
    VerificationStatus,
    utc_now_iso
)
from agent.planner import ToolPlanner
from agent.policy import PolicyEngine
from agent.reasoning import ReasoningBackend, get_reasoning_backend
from agent.tools import ToolRegistry
from agent.verifier import RecoveryVerifier
from sandbox.process_sandbox import ProcessTargetEnvironment
from telemetry.snapshot import create_incident_snapshot

class IncidentOrchestrator:
    """Orchestrates the explicit SRE incident response lifecycle:

    OBSERVE -> RETRIEVE -> CORRELATE -> HYPOTHESIZE -> PLAN -> EXECUTE -> VERIFY -> REPORT
    """

    def __init__(
        self,
        env: Optional[ProcessTargetEnvironment] = None,
        registry: Optional[ToolRegistry] = None,
        policy: Optional[PolicyEngine] = None,
        planner: Optional[ToolPlanner] = None,
        verifier: Optional[RecoveryVerifier] = None,
        correlator: Optional[EvidenceCorrelator] = None,
        reasoning_backend: Optional[ReasoningBackend] = None
    ):
        self.env = env or ProcessTargetEnvironment()
        self.registry = registry or ToolRegistry(self.env)
        self.policy = policy or PolicyEngine()
        self.planner = planner or ToolPlanner()
        self.verifier = verifier or RecoveryVerifier(self.env)
        self.correlator = correlator or EvidenceCorrelator()
        self.reasoning = reasoning_backend or get_reasoning_backend()
        self.executor = ActionExecutor(self.registry, self.policy)
        self.hypothesis_engine = HypothesisEngine()

    def investigate(
        self,
        snapshot_data: Optional[Dict[str, Any]] = None,
        incident_id: Optional[str] = None
    ) -> IncidentState:
        """Executes read-only investigation, evidence correlation, and hypothesis evaluation."""
        inc_id = incident_id or f"inc_{int(time.time() * 1000)}"
        state = IncidentState(
            incident_id=inc_id,
            started_at=utc_now_iso(),
            phase=IncidentPhase.DETECTION.value
        )

        # 1. Capture initial observation / snapshot
        if not snapshot_data:
            snapshot_data = create_incident_snapshot(self.env)

        overall_status = snapshot_data.get("overall_status", "unknown")
        state.observations.append(f"Initial target status detected as '{overall_status}'.")

        # 2. Evidence Correlation (Telemetry + RAG + Git)
        state.phase = IncidentPhase.INVESTIGATION.value
        state.observations.append("Collecting grounded evidence from telemetry, knowledge base, and Git history.")

        # Telemetry evidence
        telemetry_evidence = self.correlator.correlate_telemetry(snapshot_data)
        state.evidence.extend(telemetry_evidence)

        # RAG evidence
        query = "payment service timeout 504 gateway artificial delay"
        doc_evidence = self.correlator.correlate_knowledge(query, top_k=4)
        state.evidence.extend(doc_evidence)

        # Git evidence
        git_evidence = self.correlator.correlate_git(["timeout", "payment", "delay"], limit=3)
        state.evidence.extend(git_evidence)

        # 3. Hypothesis Generation & Evaluation
        state.phase = IncidentPhase.HYPOTHESIS_GENERATION.value
        candidates = self.hypothesis_engine.generate_candidate_hypotheses()
        state.hypotheses = candidates

        state.observations.append(f"Formulated {len(candidates)} candidate hypotheses.")

        reasoning_res = self.reasoning.reason(state.evidence, candidates)
        state.hypotheses = reasoning_res.get("evaluated_hypotheses", candidates)
        state.selected_hypothesis = reasoning_res.get("primary_hypothesis")

        if state.selected_hypothesis:
            state.observations.append(
                f"Selected supported hypothesis: '{state.selected_hypothesis.statement}' "
                f"(Confidence: {state.selected_hypothesis.confidence})."
            )
        else:
            state.observations.append("No candidate hypothesis was confirmed by empirical evidence.")

        # 4. Action Planning
        state.phase = IncidentPhase.ACTION_PLANNING.value
        if state.selected_hypothesis and state.selected_hypothesis.status == HypothesisStatus.SUPPORTED.value:
            state.planned_actions = self.planner.plan_remediation(state.selected_hypothesis, state)
            state.observations.append(f"Planned {len(state.planned_actions)} recovery actions.")
        else:
            state.observations.append("No recovery actions planned (no actionable supported hypothesis).")

        return state

    def run_incident_response(
        self,
        execute_recovery: bool = True,
        snapshot_data: Optional[Dict[str, Any]] = None,
        incident_id: Optional[str] = None
    ) -> Tuple[IncidentState, IncidentReport]:
        """Executes the full SRE incident loop: Investigate -> Remediate -> Verify -> Report."""
        if not snapshot_data:
            snapshot_data = create_incident_snapshot(self.env)

        # 1. Investigate and hypothesize
        state = self.investigate(snapshot_data=snapshot_data, incident_id=incident_id)

        # If no recovery requested or no actions planned, finish report
        if not execute_recovery or not state.planned_actions:
            report = self.generate_report(state, snapshot_data)
            return state, report

        # 2. Execute Planned Recovery Action
        state.phase = IncidentPhase.REMEDIATION.value
        state.observations.append("Entering remediation phase to execute allowlisted recovery actions.")

        recovery_succeeded = False
        for action in state.planned_actions:
            state.observations.append(f"Executing planned action: {action.tool_name} on {action.arguments}...")
            result = self.executor.execute_action(action, state)
            state.executed_actions.append(result)

            if result.status == ActionStatus.SUCCEEDED.value:
                state.observations.append(f"Action '{action.tool_name}' succeeded.")
                recovery_succeeded = True
                break
            else:
                state.observations.append(f"Action '{action.tool_name}' failed: {result.error}")

        # 3. Verification Phase
        state.phase = IncidentPhase.VERIFICATION.value
        state.observations.append("Initiating empirical verification: probing health, fault state, and live traffic.")

        verification = self.verifier.verify_recovery(
            before_snapshot=snapshot_data,
            wait_seconds=1.0,
            probe_count=4
        )
        state.verification_results.append(verification)
        state.observations.append(f"Verification completed with status: {verification.status}.")

        if verification.status == VerificationStatus.VERIFIED_RECOVERED.value:
            state.phase = IncidentPhase.RESOLVED.value
            state.final_status = "resolved"
            state.observations.append("Incident successfully resolved and verified.")
        else:
            state.phase = IncidentPhase.FAILED.value
            state.final_status = "unresolved"
            state.observations.append("Verification did not confirm full recovery.")

        report = self.generate_report(state, snapshot_data)
        return state, report

    def generate_report(
        self,
        state: IncidentState,
        initial_snapshot: Dict[str, Any]
    ) -> IncidentReport:
        """Generates an explainable postmortem report with explicit evidence citations."""
        root_cause = "Unknown / Under Investigation"
        root_cause_evidence: List[str] = []

        if state.selected_hypothesis:
            root_cause = state.selected_hypothesis.statement
            root_cause_evidence = state.selected_hypothesis.supporting_evidence_ids

        # Build evidence summary
        evidence_summary = [
            {
                "id": e.evidence_id,
                "modality": e.modality,
                "source": e.source,
                "provenance": e.provenance,
                "status": e.status
            }
            for e in state.evidence
        ]

        verification_summary = (
            state.verification_results[-1].to_dict()
            if state.verification_results
            else {"status": "not_verified", "summary": "No verification loop executed."}
        )

        return IncidentReport(
            incident_id=state.incident_id,
            started_at=state.started_at,
            ended_at=utc_now_iso(),
            initial_state={
                "overall_status": initial_snapshot.get("overall_status"),
                "service_health": initial_snapshot.get("service_health", {}),
                "fault_status": initial_snapshot.get("fault_status", {})
            },
            evidence_summary=evidence_summary,
            hypotheses=[h.to_dict() for h in state.hypotheses],
            root_cause=root_cause,
            root_cause_evidence=root_cause_evidence,
            actions_taken=[a.to_dict() for a in state.executed_actions],
            verification=verification_summary,
            final_status=state.final_status
        )
