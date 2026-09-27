from typing import Any, Dict, Optional, Set, Tuple
from agent.models import ActionStatus, IncidentPhase, IncidentState, PlannedAction, RiskLevel
from agent.tools import ALLOWED_SERVICES, ToolDefinition, ToolRegistry

class PolicyEngine:
    """Enforces zero-trust safety policies for SRE tool planning and execution."""

    def __init__(
        self,
        allow_automatic_recovery: bool = True,
        permitted_recovery_targets: Optional[Set[str]] = None
    ):
        self.allow_automatic_recovery = allow_automatic_recovery
        self.permitted_recovery_targets = permitted_recovery_targets or ALLOWED_SERVICES

    def evaluate_action(
        self,
        action: PlannedAction,
        tool: ToolDefinition,
        state: IncidentState
    ) -> Tuple[bool, str]:
        """Evaluates whether an action is permitted under the current incident phase and safety policy."""
        # 1. Read-only actions are permitted in investigation/detection/verification
        if tool.read_only:
            return True, "Read-only inspection action permitted."

        # 2. Recovery actions require REMEDIATION phase
        if state.phase != IncidentPhase.REMEDIATION.value:
            return False, f"Recovery action '{action.tool_name}' blocked: current phase is '{state.phase}', not 'remediation'."

        # 3. Recovery actions require an evaluated SUPPORTED hypothesis
        if not state.selected_hypothesis:
            return False, f"Recovery action '{action.tool_name}' blocked: no primary hypothesis has been confirmed."

        # 4. Target validation for restart_service
        if action.tool_name == "restart_service":
            target = action.arguments.get("service")
            if not target or target not in self.permitted_recovery_targets:
                return False, (
                    f"Recovery action '{action.tool_name}' blocked: unsafe target '{target}'. "
                    f"Permitted targets: {sorted(list(self.permitted_recovery_targets))}"
                )

        # 5. Check automatic recovery policy
        if not self.allow_automatic_recovery and action.requires_confirmation:
            return False, "Action requires explicit operator confirmation under strict policy."

        return True, "Recovery action authorized by safety policy."
