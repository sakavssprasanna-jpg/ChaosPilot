from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class IncidentPhase(str, Enum):
    DETECTION = "detection"
    INVESTIGATION = "investigation"
    HYPOTHESIS_GENERATION = "hypothesis_generation"
    ACTION_PLANNING = "action_planning"
    REMEDIATION = "remediation"
    VERIFICATION = "verification"
    RESOLVED = "resolved"
    FAILED = "failed"

class HypothesisStatus(str, Enum):
    CANDIDATE = "candidate"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNVERIFIED = "unverified"
    REJECTED = "rejected"

class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class RiskLevel(str, Enum):
    READ = "read"
    RECOVERY = "recovery"
    HIGH = "high"

class ActionStatus(str, Enum):
    PLANNED = "planned"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"

class VerificationStatus(str, Enum):
    VERIFIED_RECOVERED = "verified_recovered"
    PARTIALLY_RECOVERED = "partially_recovered"
    STILL_FAILING = "still_failing"
    VERIFICATION_FAILED = "verification_failed"

@dataclass
class Hypothesis:
    """Explicit hypothesis evaluated against grounded evidence."""
    id: str
    statement: str
    supporting_evidence_ids: List[str] = field(default_factory=list)
    contradicting_evidence_ids: List[str] = field(default_factory=list)
    confidence: str = ConfidenceLevel.LOW.value  # 'low', 'medium', 'high' (no fabricated probabilities)
    status: str = HypothesisStatus.CANDIDATE.value
    explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class PlannedAction:
    """A single planned action produced by the agent planner."""
    action_id: str
    tool_name: str
    arguments: Dict[str, Any]
    purpose: str
    risk: str = RiskLevel.READ.value
    requires_confirmation: bool = False
    status: str = ActionStatus.PLANNED.value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ActionResult:
    """Structured result from executing a planned tool action."""
    action_id: str
    tool_name: str
    target: str
    started_at: str
    finished_at: str
    status: str  # 'succeeded', 'failed', 'blocked'
    output: Any
    error: Optional[str] = None
    evidence_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class VerificationResult:
    """Structured empirical verification of system recovery."""
    status: str  # VerificationStatus
    checks: List[Dict[str, Any]]
    before_state: Dict[str, Any]
    after_state: Dict[str, Any]
    evidence_ids: List[str]
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class IncidentState:
    """Complete, explicit state of an active incident investigation."""
    incident_id: str
    started_at: str
    phase: str = IncidentPhase.DETECTION.value
    observations: List[str] = field(default_factory=list)
    evidence: List[Any] = field(default_factory=list)  # List[MultimodalEvidenceItem]
    hypotheses: List[Hypothesis] = field(default_factory=list)
    selected_hypothesis: Optional[Hypothesis] = None
    planned_actions: List[PlannedAction] = field(default_factory=list)
    executed_actions: List[ActionResult] = field(default_factory=list)
    verification_results: List[VerificationResult] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    directive: Optional[str] = None
    final_status: str = "open"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "started_at": self.started_at,
            "phase": self.phase,
            "directive": self.directive,
            "observations": self.observations,
            "evidence": [e.to_dict() if hasattr(e, "to_dict") else str(e) for e in self.evidence],
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "selected_hypothesis": self.selected_hypothesis.to_dict() if self.selected_hypothesis else None,
            "planned_actions": [a.to_dict() for a in self.planned_actions],
            "executed_actions": [a.to_dict() for a in self.executed_actions],
            "verification_results": [v.to_dict() for v in self.verification_results],
            "events": self.events,
            "final_status": self.final_status
        }

@dataclass
class IncidentReport:
    """Final, explainable SRE incident postmortem report."""
    incident_id: str
    started_at: str
    ended_at: str
    initial_state: Dict[str, Any]
    evidence_summary: List[Dict[str, Any]]
    hypotheses: List[Dict[str, Any]]
    root_cause: str
    root_cause_evidence: List[str]
    actions_taken: List[Dict[str, Any]]
    verification: Dict[str, Any]
    final_status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
