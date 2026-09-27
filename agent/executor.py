import time
from typing import Any, Dict, List, Optional

from agent.models import (
    ActionResult,
    ActionStatus,
    IncidentState,
    PlannedAction,
    utc_now_iso
)
from agent.policy import PolicyEngine
from agent.tools import ToolRegistry

class ActionExecutor:
    """Executes planned actions strictly via ToolRegistry and PolicyEngine."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        policy: Optional[PolicyEngine] = None
    ):
        self.registry = registry or ToolRegistry()
        self.policy = policy or PolicyEngine()

    def execute_action(
        self,
        action: PlannedAction,
        state: IncidentState
    ) -> ActionResult:
        """Safely executes an action, checking policy and capturing execution timing and structured results."""
        started_at = utc_now_iso()
        target = str(action.arguments.get("service") or action.arguments.get("query") or "system")

        tool = self.registry.get_tool(action.tool_name)
        if not tool:
            finished_at = utc_now_iso()
            action.status = ActionStatus.FAILED.value
            return ActionResult(
                action_id=action.action_id,
                tool_name=action.tool_name,
                target=target,
                started_at=started_at,
                finished_at=finished_at,
                status=ActionStatus.FAILED.value,
                output=None,
                error=f"Tool '{action.tool_name}' not found in ToolRegistry."
            )

        # Policy evaluation
        permitted, reason = self.policy.evaluate_action(action, tool, state)
        if not permitted:
            finished_at = utc_now_iso()
            action.status = ActionStatus.BLOCKED.value
            return ActionResult(
                action_id=action.action_id,
                tool_name=action.tool_name,
                target=target,
                started_at=started_at,
                finished_at=finished_at,
                status=ActionStatus.BLOCKED.value,
                output=None,
                error=reason
            )

        action.status = ActionStatus.EXECUTING.value
        try:
            output = self.registry.execute(action.tool_name, action.arguments)
            finished_at = utc_now_iso()

            # Check if underlying tool returned a failed operation flag
            if isinstance(output, dict):
                if output.get("restarted") is False:
                    action.status = ActionStatus.FAILED.value
                    return ActionResult(
                        action_id=action.action_id,
                        tool_name=action.tool_name,
                        target=target,
                        started_at=started_at,
                        finished_at=finished_at,
                        status=ActionStatus.FAILED.value,
                        output=output,
                        error=f"Service '{target}' failed to restart or health check failed."
                    )
                if output.get("reset") is False:
                    action.status = ActionStatus.FAILED.value
                    return ActionResult(
                        action_id=action.action_id,
                        tool_name=action.tool_name,
                        target=target,
                        started_at=started_at,
                        finished_at=finished_at,
                        status=ActionStatus.FAILED.value,
                        output=output,
                        error=output.get("error", "Failed to reset controlled fault.")
                    )

            action.status = ActionStatus.SUCCEEDED.value
            return ActionResult(
                action_id=action.action_id,
                tool_name=action.tool_name,
                target=target,
                started_at=started_at,
                finished_at=finished_at,
                status=ActionStatus.SUCCEEDED.value,
                output=output,
                error=None
            )

        except Exception as e:
            finished_at = utc_now_iso()
            action.status = ActionStatus.FAILED.value
            return ActionResult(
                action_id=action.action_id,
                tool_name=action.tool_name,
                target=target,
                started_at=started_at,
                finished_at=finished_at,
                status=ActionStatus.FAILED.value,
                output=None,
                error=f"Tool execution exception: {e}"
            )
