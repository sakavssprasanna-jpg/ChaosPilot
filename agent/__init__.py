"""ChaosPilot Agentic Incident Reasoning Core (Stage 5)

Provides structured, explainable SRE incident diagnosis and recovery:
- Typed IncidentState and IncidentReport models
- Granular evidence correlation from Telemetry, RAG, Git, and Multimodal
- Qualitative hypothesis engine (no fake probabilities)
- Allowlisted tool registry with zero shell execution
- Safety policy engine blocking unauthorized targets
- Read-only investigation first planner
- Safe action executor
- Empirical before/after verification loop
- Full lifecycle IncidentOrchestrator
"""

from agent.models import (
    IncidentPhase,
    HypothesisStatus,
    ConfidenceLevel,
    RiskLevel,
    ActionStatus,
    VerificationStatus,
    Hypothesis,
    PlannedAction,
    ActionResult,
    VerificationResult,
    IncidentState,
    IncidentReport
)
from agent.evidence import EvidenceCorrelator
from agent.hypotheses import HypothesisEngine
from agent.tools import ToolRegistry, ToolDefinition, ToolSecurityError, ALLOWED_SERVICES
from agent.policy import PolicyEngine
from agent.planner import ToolPlanner
from agent.executor import ActionExecutor
from agent.verifier import RecoveryVerifier
from agent.reasoning import ReasoningBackend, DeterministicReasoningBackend, GeminiReasoningBackend, get_reasoning_backend
from agent.orchestrator import IncidentOrchestrator

__all__ = [
    "IncidentPhase",
    "HypothesisStatus",
    "ConfidenceLevel",
    "RiskLevel",
    "ActionStatus",
    "VerificationStatus",
    "Hypothesis",
    "PlannedAction",
    "ActionResult",
    "VerificationResult",
    "IncidentState",
    "IncidentReport",
    "EvidenceCorrelator",
    "HypothesisEngine",
    "ToolRegistry",
    "ToolDefinition",
    "ToolSecurityError",
    "ALLOWED_SERVICES",
    "PolicyEngine",
    "ToolPlanner",
    "ActionExecutor",
    "RecoveryVerifier",
    "ReasoningBackend",
    "DeterministicReasoningBackend",
    "GeminiReasoningBackend",
    "get_reasoning_backend",
    "IncidentOrchestrator"
]
