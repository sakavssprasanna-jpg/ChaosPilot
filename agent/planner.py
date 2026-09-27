import time
from typing import List, Optional

from agent.hypotheses import HypothesisEngine
from agent.models import (
    ActionStatus,
    Hypothesis,
    HypothesisStatus,
    IncidentState,
    PlannedAction,
    RiskLevel
)

class ToolPlanner:
    """Plans ordered, deduplicated SRE tool operations following 'read-only investigation first'."""

    def plan_investigation(self, state: IncidentState) -> List[PlannedAction]:
        """Creates an ordered plan of read-only inspection actions to collect empirical evidence."""
        actions: List[PlannedAction] = []
        now_ms = int(time.time() * 1000)

        # 1. Health inspection across all services
        actions.append(PlannedAction(
            action_id=f"plan_health_{now_ms}_1",
            tool_name="inspect_health",
            arguments={},
            purpose="Verify live HTTP connectivity and health status across all microservices.",
            risk=RiskLevel.READ.value,
            requires_confirmation=False,
            status=ActionStatus.PLANNED.value
        ))

        # 2. Telemetry metrics inspection
        actions.append(PlannedAction(
            action_id=f"plan_metrics_{now_ms}_2",
            tool_name="inspect_metrics",
            arguments={"window_seconds": 300.0},
            purpose="Analyze empirical request counts, status distributions, and latency percentiles.",
            risk=RiskLevel.READ.value,
            requires_confirmation=False,
            status=ActionStatus.PLANNED.value
        ))

        # 3. Fault state inspection
        actions.append(PlannedAction(
            action_id=f"plan_fault_{now_ms}_3",
            tool_name="inspect_fault_state",
            arguments={},
            purpose="Check whether active fault injection or chaos modes are active in the target.",
            risk=RiskLevel.READ.value,
            requires_confirmation=False,
            status=ActionStatus.PLANNED.value
        ))

        # 4. RAG Runbook retrieval
        actions.append(PlannedAction(
            action_id=f"plan_runbook_{now_ms}_4",
            tool_name="retrieve_runbook",
            arguments={"query": "payment timeout 504 gateway", "top_k": 3},
            purpose="Retrieve authoritative runbook remediation procedures from knowledge base.",
            risk=RiskLevel.READ.value,
            requires_confirmation=False,
            status=ActionStatus.PLANNED.value
        ))

        # 5. Architecture topology retrieval
        actions.append(PlannedAction(
            action_id=f"plan_arch_{now_ms}_5",
            tool_name="retrieve_architecture",
            arguments={"query": "service topology timeout SLA budget", "top_k": 2},
            purpose="Retrieve service dependency graph and timeout budget specifications.",
            risk=RiskLevel.READ.value,
            requires_confirmation=False,
            status=ActionStatus.PLANNED.value
        ))

        # 6. Git changes inspection
        actions.append(PlannedAction(
            action_id=f"plan_git_{now_ms}_6",
            tool_name="inspect_git_changes",
            arguments={"query_terms": ["timeout", "payment", "delay", "budget"], "limit": 2},
            purpose="Verify whether recent git commits introduced relevant configuration changes.",
            risk=RiskLevel.READ.value,
            requires_confirmation=False,
            status=ActionStatus.PLANNED.value
        ))

        return actions

    def plan_remediation(
        self,
        hypothesis: Hypothesis,
        state: IncidentState
    ) -> List[PlannedAction]:
        """Plans safe, targeted recovery actions only after an empirical hypothesis is supported."""
        actions: List[PlannedAction] = []
        now_ms = int(time.time() * 1000)

        if hypothesis.status != HypothesisStatus.SUPPORTED.value:
            return []

        if hypothesis.id == HypothesisEngine.HYPOTHESIS_PAYMENT_TIMEOUT:
            # Safe recovery option 1: reset controlled fault hook
            actions.append(PlannedAction(
                action_id=f"plan_reset_fault_{now_ms}",
                tool_name="reset_controlled_fault",
                arguments={},
                purpose="Reset Payment Service artificial processing delay to restore nominal upstream response times.",
                risk=RiskLevel.RECOVERY.value,
                requires_confirmation=False,
                status=ActionStatus.PLANNED.value
            ))
            # Safe recovery option 2: restart payment-service if reset is insufficient
            actions.append(PlannedAction(
                action_id=f"plan_restart_payment_{now_ms}",
                tool_name="restart_service",
                arguments={"service": "payment-service"},
                purpose="Restart Payment Service to clean process state and purge artificial delay configuration.",
                risk=RiskLevel.RECOVERY.value,
                requires_confirmation=False,
                status=ActionStatus.PLANNED.value
            ))

        elif hypothesis.id == HypothesisEngine.HYPOTHESIS_ORDER_CRASH:
            actions.append(PlannedAction(
                action_id=f"plan_restart_order_{now_ms}",
                tool_name="restart_service",
                arguments={"service": "order-service"},
                purpose="Restart Order Service to recover from process failure or socket hang.",
                risk=RiskLevel.RECOVERY.value,
                requires_confirmation=False,
                status=ActionStatus.PLANNED.value
            ))

        elif hypothesis.id == HypothesisEngine.HYPOTHESIS_GATEWAY_MISCONFIG:
            actions.append(PlannedAction(
                action_id=f"plan_restart_gateway_{now_ms}",
                tool_name="restart_service",
                arguments={"service": "gateway"},
                purpose="Restart API Gateway to reload clean configuration state.",
                risk=RiskLevel.RECOVERY.value,
                requires_confirmation=False,
                status=ActionStatus.PLANNED.value
            ))

        return actions
