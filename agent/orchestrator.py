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
from agent.evidence_graph import EvidenceGraphBuilder

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
        reasoning_backend: Optional[ReasoningBackend] = None,
        event_callback: Optional[Any] = None
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
        self.event_callback = event_callback

    def _emit(
        self,
        state: IncidentState,
        stage: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        callback: Optional[Any] = None
    ):
        event = {
            "timestamp": utc_now_iso(),
            "stage": stage,
            "message": message,
            "details": details or {}
        }
        state.events.append(event)
        cb = callback or self.event_callback
        if cb and callable(cb):
            try:
                cb(event)
            except Exception:
                pass

    def investigate(
        self,
        snapshot_data: Optional[Dict[str, Any]] = None,
        incident_id: Optional[str] = None,
        event_callback: Optional[Any] = None,
        directive: Optional[str] = None
    ) -> IncidentState:
        """Executes read-only investigation, evidence correlation, and hypothesis evaluation."""
        inc_id = incident_id or f"inc_{int(time.time() * 1000)}"
        state = IncidentState(
            incident_id=inc_id,
            started_at=utc_now_iso(),
            phase=IncidentPhase.DETECTION.value,
            directive=directive
        )
        cb = event_callback or self.event_callback

        if directive:
            dir_msg = f"Operator directive received: \"{directive}\""
            state.observations.append(dir_msg)
            self._emit(state, "directive", dir_msg, {"directive": directive}, cb)

        # 1. Capture initial observation / snapshot
        if not snapshot_data:
            snapshot_data = create_incident_snapshot(self.env)

        overall_status = snapshot_data.get("overall_status", "unknown")
        obs_msg = f"Initial target status detected as '{overall_status}'."
        state.observations.append(obs_msg)
        self._emit(state, "detection", obs_msg, {"status": overall_status}, cb)

        # 2. Evidence Correlation (Telemetry + RAG + Git)
        state.phase = IncidentPhase.INVESTIGATION.value
        inv_msg = "Collecting grounded evidence from telemetry, knowledge base, and Git history."
        state.observations.append(inv_msg)
        self._emit(state, "investigation", inv_msg, {}, cb)

        # Telemetry evidence
        telemetry_evidence = self.correlator.correlate_telemetry(snapshot_data)
        state.evidence.extend(telemetry_evidence)
        self._emit(state, "telemetry", f"Correlated {len(telemetry_evidence)} telemetry metric items.", {"count": len(telemetry_evidence)}, cb)

        # RAG evidence
        query = "payment service timeout 504 gateway artificial delay"
        doc_evidence = self.correlator.correlate_knowledge(query, top_k=4)
        state.evidence.extend(doc_evidence)
        self._emit(state, "rag", f"Retrieved {len(doc_evidence)} grounded knowledge chunks.", {"count": len(doc_evidence)}, cb)

        # Git evidence
        git_evidence = self.correlator.correlate_git(["timeout", "payment", "delay"], limit=3)
        state.evidence.extend(git_evidence)
        self._emit(state, "git", f"Retrieved {len(git_evidence)} isolated Git commits.", {"count": len(git_evidence)}, cb)

        # 3. Hypothesis Generation & Evaluation
        state.phase = IncidentPhase.HYPOTHESIS_GENERATION.value
        candidates = self.hypothesis_engine.generate_candidate_hypotheses()
        state.hypotheses = candidates

        hyp_msg = f"Formulated {len(candidates)} candidate hypotheses."
        state.observations.append(hyp_msg)
        self._emit(state, "hypotheses", hyp_msg, {"count": len(candidates)}, cb)

        reasoning_res = self.reasoning.reason(state.evidence, candidates)
        state.hypotheses = reasoning_res.get("evaluated_hypotheses", candidates)
        state.selected_hypothesis = reasoning_res.get("primary_hypothesis")

        if state.selected_hypothesis:
            sel_msg = (
                f"Selected supported hypothesis: '{state.selected_hypothesis.statement}' "
                f"(Confidence: {state.selected_hypothesis.confidence})."
            )
            state.observations.append(sel_msg)
            self._emit(state, "hypothesis_selected", sel_msg, {"hypothesis": state.selected_hypothesis.id}, cb)
        else:
            no_hyp_msg = "No candidate hypothesis was confirmed by empirical evidence."
            state.observations.append(no_hyp_msg)
            self._emit(state, "hypothesis_selected", no_hyp_msg, {}, cb)

        # 4. Action Planning
        state.phase = IncidentPhase.ACTION_PLANNING.value
        if state.selected_hypothesis and state.selected_hypothesis.status == HypothesisStatus.SUPPORTED.value:
            state.planned_actions = self.planner.plan_remediation(state.selected_hypothesis, state)
            plan_msg = f"Planned {len(state.planned_actions)} recovery actions."
            state.observations.append(plan_msg)
            self._emit(state, "action_planning", plan_msg, {"actions": [a.tool_name for a in state.planned_actions]}, cb)
        else:
            no_plan_msg = "No recovery actions planned (no actionable supported hypothesis)."
            state.observations.append(no_plan_msg)
            self._emit(state, "action_planning", no_plan_msg, {}, cb)

        # 5. Evidence Graph & Grounded Claims
        graph_builder = EvidenceGraphBuilder()
        graph = graph_builder.build_graph(state, snapshot=snapshot_data)
        state.evidence_graph = graph.to_dict()
        state.claims = [c.to_dict() for c in graph.claims.values()]
        eg_msg = f"Constructed empirical evidence graph ({len(graph.nodes)} nodes, {len(graph.edges)} edges, {len(graph.claims)} claims)."
        state.observations.append(eg_msg)
        self._emit(state, "evidence_graph", eg_msg, {
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "claim_count": len(graph.claims)
        }, cb)

        return state

    def run_incident_response(
        self,
        execute_recovery: bool = True,
        snapshot_data: Optional[Dict[str, Any]] = None,
        incident_id: Optional[str] = None,
        event_callback: Optional[Any] = None,
        directive: Optional[str] = None
    ) -> Tuple[IncidentState, IncidentReport]:
        """Executes the full SRE incident loop: Investigate -> Remediate -> Verify -> Report."""
        cb = event_callback or self.event_callback
        if not snapshot_data:
            snapshot_data = create_incident_snapshot(self.env)

        # 1. Investigate and hypothesize
        state = self.investigate(snapshot_data=snapshot_data, incident_id=incident_id, event_callback=cb, directive=directive)

        # If no recovery requested or no actions planned, finish report
        if not execute_recovery or not state.planned_actions:
            report = self.generate_report(state, snapshot_data)
            return state, report

        # 2. Execute Planned Recovery Action
        state.phase = IncidentPhase.REMEDIATION.value
        rem_msg = "Entering remediation phase to execute allowlisted recovery actions."
        state.observations.append(rem_msg)
        self._emit(state, "remediation", rem_msg, {}, cb)

        recovery_succeeded = False
        for action in state.planned_actions:
            act_start_msg = f"Executing planned action: {action.tool_name} on {action.arguments}..."
            state.observations.append(act_start_msg)
            self._emit(state, "action_execution", act_start_msg, {"action": action.tool_name}, cb)

            result = self.executor.execute_action(action, state)
            state.executed_actions.append(result)

            if result.status == ActionStatus.SUCCEEDED.value:
                act_ok_msg = f"Action '{action.tool_name}' succeeded."
                state.observations.append(act_ok_msg)
                self._emit(state, "action_result", act_ok_msg, {"status": "succeeded"}, cb)
                recovery_succeeded = True
                break
            else:
                act_fail_msg = f"Action '{action.tool_name}' failed: {result.error}"
                state.observations.append(act_fail_msg)
                self._emit(state, "action_result", act_fail_msg, {"status": "failed", "error": result.error}, cb)

        # 3. Verification Phase
        state.phase = IncidentPhase.VERIFICATION.value
        ver_msg = "Initiating empirical verification: probing health, fault state, and live traffic."
        state.observations.append(ver_msg)
        self._emit(state, "verification", ver_msg, {}, cb)

        verification = self.verifier.verify_recovery(
            before_snapshot=snapshot_data,
            wait_seconds=1.0,
            probe_count=4
        )
        state.verification_results.append(verification)
        ver_done_msg = f"Verification completed with status: {verification.status}."
        state.observations.append(ver_done_msg)
        self._emit(state, "verification_result", ver_done_msg, {"status": verification.status}, cb)

        if verification.status == VerificationStatus.VERIFIED_RECOVERED.value:
            state.phase = IncidentPhase.RESOLVED.value
            state.final_status = "resolved"
            res_msg = "Incident successfully resolved and verified."
            state.observations.append(res_msg)
            self._emit(state, "resolved", res_msg, {"status": "resolved"}, cb)
        else:
            state.phase = IncidentPhase.FAILED.value
            state.final_status = "unresolved"
            fail_msg = "Verification did not confirm full recovery."
            state.observations.append(fail_msg)
            self._emit(state, "failed", fail_msg, {"status": "unresolved"}, cb)

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

        # Update evidence graph with latest actions & verification
        graph_builder = EvidenceGraphBuilder()
        graph = graph_builder.build_graph(state, snapshot=initial_snapshot)
        state.evidence_graph = graph.to_dict()
        state.claims = [c.to_dict() for c in graph.claims.values()]

        evidence_graph_summary = {
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "claim_count": len(graph.claims)
        }

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
            final_status=state.final_status,
            evidence_graph_summary=evidence_graph_summary
        )
