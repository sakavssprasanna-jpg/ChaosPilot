from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
import httpx

from agent.models import RiskLevel
from sandbox.process_sandbox import ProcessTargetEnvironment
from services.config import PAYMENT_SERVICE_URL, SERVICE_MAP
from telemetry.db import get_all_summaries, get_summary
from telemetry.snapshot import get_fault_status
from rag.retriever import retrieve
from git_ops.diff_analyzer import get_git_analyzer

ALLOWED_SERVICES = set(SERVICE_MAP.keys())

class ToolSecurityError(ValueError):
    """Raised when an action violates SRE tool security or allowlist rules."""
    pass

@dataclass
class ToolDefinition:
    """Explicit definition of an allowlisted SRE tool."""
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    risk_level: str  # RiskLevel: 'read', 'recovery', 'high'
    read_only: bool
    handler: Callable[..., Any]

class ToolRegistry:
    """Strict registry containing only explicitly authorized SRE inspection and recovery tools."""

    def __init__(self, env: Optional[ProcessTargetEnvironment] = None):
        self.env = env or ProcessTargetEnvironment()
        self._tools: Dict[str, ToolDefinition] = {}
        self._register_default_tools()

    def _validate_service_arg(self, service: Optional[str]) -> Optional[str]:
        if service is not None and service not in ALLOWED_SERVICES:
            raise ToolSecurityError(
                f"Unauthorized service '{service}'. Must be one of: {sorted(list(ALLOWED_SERVICES))}"
            )
        return service

    def _register_default_tools(self):
        # 1. inspect_health
        self.register(ToolDefinition(
            name="inspect_health",
            description="Inspects real HTTP /health status of one or all allowlisted microservices.",
            input_schema={"service": {"type": "string", "required": False, "allowed": list(ALLOWED_SERVICES)}},
            output_schema={"health_data": "dict"},
            risk_level=RiskLevel.READ.value,
            read_only=True,
            handler=self._tool_inspect_health
        ))

        # 2. inspect_metrics
        self.register(ToolDefinition(
            name="inspect_metrics",
            description="Queries real request telemetry, error counts, latency percentiles, and status distributions.",
            input_schema={
                "service": {"type": "string", "required": False, "allowed": list(ALLOWED_SERVICES)},
                "window_seconds": {"type": "number", "required": False, "default": 300.0}
            },
            output_schema={"metrics_summary": "dict"},
            risk_level=RiskLevel.READ.value,
            read_only=True,
            handler=self._tool_inspect_metrics
        ))

        # 3. inspect_recent_logs
        self.register(ToolDefinition(
            name="inspect_recent_logs",
            description="Tails the last N lines of stdout/stderr logs from an allowlisted service.",
            input_schema={
                "service": {"type": "string", "required": True, "allowed": list(ALLOWED_SERVICES)},
                "lines": {"type": "integer", "required": False, "default": 30}
            },
            output_schema={"log_lines": "list"},
            risk_level=RiskLevel.READ.value,
            read_only=True,
            handler=self._tool_inspect_recent_logs
        ))

        # 4. inspect_fault_state
        self.register(ToolDefinition(
            name="inspect_fault_state",
            description="Directly inspects active chaos/fault injection state from Payment Service.",
            input_schema={},
            output_schema={"fault_status": "dict"},
            risk_level=RiskLevel.READ.value,
            read_only=True,
            handler=self._tool_inspect_fault_state
        ))

        # 5. retrieve_runbook
        self.register(ToolDefinition(
            name="retrieve_runbook",
            description="Retrieves grounded operational runbooks and failure remediation procedures from knowledge base.",
            input_schema={
                "query": {"type": "string", "required": True},
                "top_k": {"type": "integer", "required": False, "default": 3}
            },
            output_schema={"citations": "list"},
            risk_level=RiskLevel.READ.value,
            read_only=True,
            handler=self._tool_retrieve_runbook
        ))

        # 6. retrieve_architecture
        self.register(ToolDefinition(
            name="retrieve_architecture",
            description="Retrieves grounded architectural specifications, SLA timeout budgets, and topology docs.",
            input_schema={
                "query": {"type": "string", "required": True},
                "top_k": {"type": "integer", "required": False, "default": 3}
            },
            output_schema={"citations": "list"},
            risk_level=RiskLevel.READ.value,
            read_only=True,
            handler=self._tool_retrieve_architecture
        ))

        # 7. inspect_git_changes
        self.register(ToolDefinition(
            name="inspect_git_changes",
            description="Queries isolated local git repository commits and diff patches matching diagnostic terms.",
            input_schema={
                "query_terms": {"type": "list", "required": False, "default": []},
                "limit": {"type": "integer", "required": False, "default": 3}
            },
            output_schema={"git_commits": "list"},
            risk_level=RiskLevel.READ.value,
            read_only=True,
            handler=self._tool_inspect_git_changes
        ))

        # 8. probe_traffic
        self.register(ToolDefinition(
            name="probe_traffic",
            description="Sends a small bounded probe traffic burst to Gateway to empirically test end-to-end checkout flow.",
            input_schema={
                "count": {"type": "integer", "required": False, "default": 3},
                "concurrency": {"type": "integer", "required": False, "default": 1}
            },
            output_schema={"probe_result": "dict"},
            risk_level=RiskLevel.READ.value,
            read_only=True,
            handler=self._tool_probe_traffic
        ))

        # 9. restart_service (Controlled Recovery)
        self.register(ToolDefinition(
            name="restart_service",
            description="Safely restarts a degraded microservice using the isolated Process Sandbox.",
            input_schema={
                "service": {"type": "string", "required": True, "allowed": list(ALLOWED_SERVICES)}
            },
            output_schema={"restarted": "bool", "service": "string"},
            risk_level=RiskLevel.RECOVERY.value,
            read_only=False,
            handler=self._tool_restart_service
        ))

        # 10. reset_controlled_fault (Controlled Recovery)
        self.register(ToolDefinition(
            name="reset_controlled_fault",
            description="Calls Payment Service /fault/reset to clear injected delays and restore nominal operation.",
            input_schema={},
            output_schema={"reset": "bool", "message": "string"},
            risk_level=RiskLevel.RECOVERY.value,
            read_only=False,
            handler=self._tool_reset_controlled_fault
        ))

    def register(self, tool: ToolDefinition):
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def list_tools(self) -> List[ToolDefinition]:
        return list(self._tools.values())

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        tool = self.get_tool(tool_name)
        if not tool:
            raise ToolSecurityError(f"Tool '{tool_name}' is not registered in allowlisted SRE tools.")

        # Strict argument validation
        self._validate_arguments(tool, arguments)

        # Direct execution of registered Python function
        return tool.handler(**arguments)

    def _validate_arguments(self, tool: ToolDefinition, arguments: Dict[str, Any]):
        schema = tool.input_schema
        # Check for unknown parameters
        for arg in arguments:
            if arg not in schema:
                raise ToolSecurityError(f"Unexpected argument '{arg}' for tool '{tool.name}'")

        # Check required parameters
        for param, rules in schema.items():
            if rules.get("required") and param not in arguments:
                raise ToolSecurityError(f"Missing required argument '{param}' for tool '{tool.name}'")
            if param in arguments and "allowed" in rules:
                val = arguments[param]
                if val not in rules["allowed"]:
                    raise ToolSecurityError(
                        f"Invalid value '{val}' for argument '{param}'. Allowed: {rules['allowed']}"
                    )

    # ---------------- Tool Handlers ----------------

    def _tool_inspect_health(self, service: Optional[str] = None) -> Dict[str, Any]:
        self._validate_service_arg(service)
        if service:
            return {service: self.env.check_health(service)}
        return self.env.check_all_health()

    def _tool_inspect_metrics(self, service: Optional[str] = None, window_seconds: float = 300.0) -> Dict[str, Any]:
        self._validate_service_arg(service)
        if service:
            return {service: get_summary(service, window_seconds=window_seconds)}
        return get_all_summaries(window_seconds=window_seconds)

    def _tool_inspect_recent_logs(self, service: str, lines: int = 30) -> List[str]:
        self._validate_service_arg(service)
        return self.env.get_logs(service, lines=lines)

    def _tool_inspect_fault_state(self) -> Dict[str, Any]:
        return get_fault_status()

    def _tool_retrieve_runbook(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        citations = retrieve(query, top_k=top_k)
        return [c.to_dict() for c in citations if c.source_type == "runbook"]

    def _tool_retrieve_architecture(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        citations = retrieve(query, top_k=top_k)
        return [c.to_dict() for c in citations if c.source_type == "architecture"]

    def _tool_inspect_git_changes(self, query_terms: Optional[List[str]] = None, limit: int = 3) -> List[Dict[str, Any]]:
        terms = query_terms or ["timeout", "payment", "gateway", "fault"]
        analyzer = get_git_analyzer()
        commits = analyzer.search_commits(terms, limit=limit)
        return [c.to_dict() for c in commits]

    def _tool_probe_traffic(self, count: int = 3, concurrency: int = 1) -> Dict[str, Any]:
        from telemetry.traffic import generate_traffic
        bounded_count = min(max(count, 1), 10)  # Safe bounding
        bounded_concurrency = min(max(concurrency, 1), 2)
        return generate_traffic(num_requests=bounded_count, concurrency=concurrency, interval_seconds=0.05)

    def _tool_restart_service(self, service: str) -> Dict[str, Any]:
        self._validate_service_arg(service)
        success = self.env.restart_service(service)
        return {"service": service, "restarted": success}

    def _tool_reset_controlled_fault(self) -> Dict[str, Any]:
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.post(f"{PAYMENT_SERVICE_URL}/fault/reset")
                return {"reset": resp.status_code == 200, "details": resp.json()}
        except Exception as e:
            return {"reset": False, "error": str(e)}
