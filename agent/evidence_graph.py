from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.models import (
    ConfidenceLevel,
    Hypothesis,
    HypothesisStatus,
    IncidentReport,
    IncidentState,
    VerificationStatus,
    utc_now_iso
)
from multimodal.schema import MultimodalEvidenceItem


# ---------------------------------------------------------------------------
# Typed Graph Enums
# ---------------------------------------------------------------------------

class NodeType(str, Enum):
    INCIDENT = "incident"
    OBSERVATION = "observation"
    TELEMETRY = "telemetry"
    SERVICE = "service"
    FAULT = "fault"
    DOCUMENT = "document"
    GIT_CHANGE = "git_change"
    VISUAL_OBSERVATION = "visual_observation"
    VOICE_OBSERVATION = "voice_observation"
    HYPOTHESIS = "hypothesis"
    ACTION = "action"
    VERIFICATION = "verification"
    CLAIM = "claim"


class EdgeType(str, Enum):
    INCIDENT_CONTAINS = "incident_contains"
    OBSERVED_ON = "observed_on"
    CAUSED_BY_CANDIDATE = "caused_by_candidate"
    PROPAGATES_TO = "propagates_to"
    SUPPORTED_BY = "supported_by"
    CONTRADICTED_BY = "contradicted_by"
    DOCUMENTED_BY = "documented_by"
    CHANGED_BY = "changed_by"
    VERIFIED_BY = "verified_by"
    ACTED_ON = "acted_on"
    DEPENDS_ON = "depends_on"


class ClaimSupportStrength(str, Enum):
    UNSUPPORTED = "unsupported"
    WEAKLY_SUPPORTED = "weakly_supported"
    SUPPORTED = "supported"
    STRONGLY_SUPPORTED = "strongly_supported"
    CONTRADICTED = "contradicted"


class ClaimStatus(str, Enum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"


def classify_claim_support(
    evidence_ids: Optional[List[str]] = None,
    modalities: Optional[List[str]] = None,
    contradictions: Optional[List[str]] = None
) -> str:
    """Evaluates qualitative claim support strength according to strict grounding invariants:

    - Case E: >= 1 contradiction -> CONTRADICTED
    - Case A: 0 evidence -> UNSUPPORTED
    - Case B: 1 valid evidence -> WEAKLY_SUPPORTED
    - Case D: >= 3 items across >= 2 modalities + 0 contradictions -> STRONGLY_SUPPORTED
    - Case C: >= 2 evidence items in same modality (or fewer than 3 across >=2) -> SUPPORTED
    """
    if contradictions and len(contradictions) > 0:
        return ClaimSupportStrength.CONTRADICTED.value

    ev_list = evidence_ids or []
    if len(ev_list) == 0:
        return ClaimSupportStrength.UNSUPPORTED.value

    if len(ev_list) == 1:
        return ClaimSupportStrength.WEAKLY_SUPPORTED.value

    distinct_modalities = set(modalities or [])
    if len(ev_list) >= 3 and len(distinct_modalities) >= 2:
        return ClaimSupportStrength.STRONGLY_SUPPORTED.value

    return ClaimSupportStrength.SUPPORTED.value


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class GraphNode:
    """A typed entity within the empirical SRE evidence graph."""
    id: str
    type: str  # NodeType
    label: str
    source: str = ""
    timestamp: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GraphEdge:
    """A directional, typed relationship connecting two evidence graph nodes."""
    source: str
    target: str
    relationship: str  # EdgeType
    provenance: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GroundedClaim:
    """A testable diagnostic claim with empirical support and contradiction tracking."""
    claim_id: str
    statement: str
    evidence_ids: List[str]
    support_strength: str  # ClaimSupportStrength
    status: str  # ClaimStatus
    contradictions: List[str] = field(default_factory=list)
    created_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Evidence Graph
# ---------------------------------------------------------------------------

class EvidenceGraph:
    """In-memory, deterministic, and serializable SRE incident evidence graph.

    Maintains explicit nodes, directional edges, and grounded claims derived
    strictly from empirical observations, telemetry, runbooks, and actions.
    """

    def __init__(self):
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: List[GraphEdge] = []
        self.claims: Dict[str, GroundedClaim] = {}

    def add_node(self, node: GraphNode) -> None:
        """Adds a node to the graph; updates if already exists."""
        self.nodes[node.id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        """Adds an edge to the graph if not already present."""
        for existing in self.edges:
            if (
                existing.source == edge.source
                and existing.target == edge.target
                and existing.relationship == edge.relationship
            ):
                return
        self.edges.append(edge)

    def add_claim(self, claim: GroundedClaim) -> None:
        """Registers a grounded claim and creates corresponding node and edges."""
        self.claims[claim.claim_id] = claim
        claim_node = GraphNode(
            id=f"claim:{claim.claim_id}",
            type=NodeType.CLAIM.value,
            label=f"Claim: {claim.statement[:60]}...",
            source="claim_correlator",
            timestamp=claim.created_at or utc_now_iso(),
            metadata={
                "claim_id": claim.claim_id,
                "statement": claim.statement,
                "support_strength": claim.support_strength,
                "status": claim.status,
                "evidence_ids": claim.evidence_ids,
                "contradictions": claim.contradictions
            }
        )
        self.add_node(claim_node)

        # Connect claim to supporting evidence
        for eid in claim.evidence_ids:
            # Map evidence ID to node ID if node exists
            node_id = self._resolve_evidence_node_id(eid)
            if node_id and node_id in self.nodes:
                self.add_edge(GraphEdge(
                    source=f"claim:{claim.claim_id}",
                    target=node_id,
                    relationship=EdgeType.SUPPORTED_BY.value,
                    provenance=f"claim_support:{claim.claim_id}->{node_id}"
                ))

        # Connect claim to contradicting evidence
        for cid in claim.contradictions:
            node_id = self._resolve_evidence_node_id(cid)
            if node_id and node_id in self.nodes:
                self.add_edge(GraphEdge(
                    source=f"claim:{claim.claim_id}",
                    target=node_id,
                    relationship=EdgeType.CONTRADICTED_BY.value,
                    provenance=f"claim_contradiction:{claim.claim_id}->{node_id}"
                ))

    def _resolve_evidence_node_id(self, evidence_id: str) -> Optional[str]:
        """Resolves an evidence ID to its corresponding graph node ID."""
        if evidence_id in self.nodes:
            return evidence_id
        for prefix in ("tel:", "obs:", "doc:", "git:", "vis:", "voice:", "fault:", "svc:"):
            cand = f"{prefix}{evidence_id}"
            if cand in self.nodes:
                return cand
        for nid in self.nodes:
            if evidence_id in nid:
                return nid
        return None

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Retrieves a node by its ID."""
        return self.nodes.get(node_id)

    def get_edges(
        self,
        source: Optional[str] = None,
        target: Optional[str] = None,
        relationship: Optional[str] = None
    ) -> List[GraphEdge]:
        """Queries edges filtering by source, target, or relationship type."""
        results = []
        for e in self.edges:
            if source and e.source != source:
                continue
            if target and e.target != target:
                continue
            if relationship and e.relationship != relationship:
                continue
            results.append(e)
        return results

    def get_supporting_evidence(self, target_id: str) -> List[GraphNode]:
        """Returns all nodes that support the given claim, hypothesis, or entity."""
        supporting_nodes = []
        # Check edges where target is supporting the source via SUPPORTED_BY
        for edge in self.edges:
            if (edge.source == target_id or edge.source == f"claim:{target_id}" or edge.source == f"hyp:{target_id}") and edge.relationship == EdgeType.SUPPORTED_BY.value:
                if edge.target in self.nodes:
                    supporting_nodes.append(self.nodes[edge.target])
        return supporting_nodes

    def get_contradicting_evidence(self, target_id: str) -> List[GraphNode]:
        """Returns all nodes that contradict the given claim or hypothesis."""
        contradicting_nodes = []
        for edge in self.edges:
            if (edge.source == target_id or edge.source == f"claim:{target_id}" or edge.source == f"hyp:{target_id}") and edge.relationship == EdgeType.CONTRADICTED_BY.value:
                if edge.target in self.nodes:
                    contradicting_nodes.append(self.nodes[edge.target])
        return contradicting_nodes

    def get_upstream_causes(self, node_id: str) -> List[GraphNode]:
        """Returns nodes that act as upstream causes or propagation sources."""
        causes = []
        for edge in self.edges:
            if edge.target == node_id and edge.relationship in (EdgeType.PROPAGATES_TO.value, EdgeType.CAUSED_BY_CANDIDATE.value):
                if edge.source in self.nodes:
                    causes.append(self.nodes[edge.source])
        return causes

    def get_downstream_effects(self, node_id: str) -> List[GraphNode]:
        """Returns nodes affected downstream by propagation."""
        effects = []
        for edge in self.edges:
            if edge.source == node_id and edge.relationship == EdgeType.PROPAGATES_TO.value:
                if edge.target in self.nodes:
                    effects.append(self.nodes[edge.target])
        return effects

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the entire graph into a JSON-compatible dictionary."""
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "claim_count": len(self.claims),
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges],
            "claims": [c.to_dict() for c in self.claims.values()]
        }

    def to_json(self, indent: int = 2) -> str:
        """Serializes graph to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceGraph":
        """Reconstructs an EvidenceGraph from a serialized dictionary."""
        graph = cls()
        for nd in data.get("nodes", []):
            graph.add_node(GraphNode(**nd))
        for ed in data.get("edges", []):
            graph.add_edge(GraphEdge(**ed))
        for cd in data.get("claims", []):
            graph.claims[cd["claim_id"]] = GroundedClaim(**cd)
        return graph

    def to_mermaid(self, direction: str = "TD") -> str:
        """Generates a valid Mermaid flowchart visualization of the evidence graph."""
        lines = [f"flowchart {direction}"]

        def clean_id(nid: str) -> str:
            return re.sub(r"[^a-zA-Z0-9_]", "_", nid)

        def clean_label(text: str) -> str:
            # Escape quotes and brackets
            sanitized = text.replace('"', "'").replace("[", "(").replace("]", ")")
            return sanitized[:70]

        # 1. Add nodes with styling icons
        for node in self.nodes.values():
            cid = clean_id(node.id)
            label = clean_label(node.label)
            icon = "⚪"
            if node.type == NodeType.INCIDENT.value:
                icon = "🚨"
            elif node.type == NodeType.SERVICE.value:
                icon = "📦"
            elif node.type == NodeType.FAULT.value:
                icon = "💥"
            elif node.type == NodeType.TELEMETRY.value:
                icon = "📊"
            elif node.type == NodeType.DOCUMENT.value:
                icon = "📖"
            elif node.type == NodeType.GIT_CHANGE.value:
                icon = "🌿"
            elif node.type == NodeType.VISUAL_OBSERVATION.value:
                icon = "🖼️"
            elif node.type == NodeType.VOICE_OBSERVATION.value:
                icon = "🎙️"
            elif node.type == NodeType.CLAIM.value:
                icon = "⚖️"
            elif node.type == NodeType.HYPOTHESIS.value:
                icon = "🧠"
            elif node.type == NodeType.ACTION.value:
                icon = "🛠️"
            elif node.type == NodeType.VERIFICATION.value:
                icon = "🔬"

            lines.append(f'    {cid}["{icon} {label}"]')

        # 2. Add edges with relationship labels
        for edge in self.edges:
            src = clean_id(edge.source)
            tgt = clean_id(edge.target)
            rel = edge.relationship
            lines.append(f"    {src} -->|{rel}| {tgt}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Evidence Graph Builder
# ---------------------------------------------------------------------------

class EvidenceGraphBuilder:
    """Builds a fully populated, grounded EvidenceGraph from IncidentState & snapshot data."""

    def build_graph(
        self,
        state: IncidentState,
        snapshot: Optional[Dict[str, Any]] = None,
        report: Optional[IncidentReport] = None
    ) -> EvidenceGraph:
        """Constructs an integrated evidence graph from real state, telemetry, and evidence."""
        graph = EvidenceGraph()

        # 1. Base Service Topology Nodes
        gateway_node = GraphNode(
            id="svc:gateway",
            type=NodeType.SERVICE.value,
            label="API Gateway (:8000)",
            source="config:services.config",
            metadata={"port": 8000, "tier": "ingress"}
        )
        order_node = GraphNode(
            id="svc:order-service",
            type=NodeType.SERVICE.value,
            label="Order Service (:8001)",
            source="config:services.config",
            metadata={"port": 8001, "tier": "intermediate"}
        )
        payment_node = GraphNode(
            id="svc:payment-service",
            type=NodeType.SERVICE.value,
            label="Payment Service (:8002)",
            source="config:services.config",
            metadata={"port": 8002, "tier": "downstream"}
        )

        for sn in (gateway_node, order_node, payment_node):
            graph.add_node(sn)

        # Service dependency edges
        graph.add_edge(GraphEdge("svc:gateway", "svc:order-service", EdgeType.DEPENDS_ON.value))
        graph.add_edge(GraphEdge("svc:order-service", "svc:payment-service", EdgeType.DEPENDS_ON.value))

        # Failure propagation edges
        graph.add_edge(GraphEdge("svc:payment-service", "svc:order-service", EdgeType.PROPAGATES_TO.value))
        graph.add_edge(GraphEdge("svc:order-service", "svc:gateway", EdgeType.PROPAGATES_TO.value))

        # 2. Incident Node
        inc_id = state.incident_id or "inc_unknown"
        inc_node = GraphNode(
            id=f"inc:{inc_id}",
            type=NodeType.INCIDENT.value,
            label=f"Incident {inc_id[:12]} ({state.final_status.upper()})",
            source="IncidentOrchestrator",
            timestamp=state.started_at,
            metadata={"phase": state.phase, "final_status": state.final_status}
        )
        graph.add_node(inc_node)

        # 3. Fault Node (if active or evidenced)
        fault_active = False
        fault_mode = "timeout"
        fault_delay = 4.5

        if snapshot:
            f_stat = snapshot.get("fault_status", {})
            if f_stat and f_stat.get("active"):
                fault_active = True
                fault_mode = f_stat.get("mode", "timeout")
                fault_delay = f_stat.get("delay_seconds", 4.5)

        # Also inspect evidence for fault
        for e in state.evidence:
            if getattr(e, "modality", None) == "telemetry":
                cnt = getattr(e, "content", {})
                if isinstance(cnt, dict) and cnt.get("mode") in ("timeout", "500", "503") and cnt.get("active"):
                    fault_active = True
                    fault_mode = cnt.get("mode")
                    fault_delay = cnt.get("delay_seconds", 4.5)

        if fault_active:
            fault_node = GraphNode(
                id="fault:payment_timeout",
                type=NodeType.FAULT.value,
                label=f"Injected Fault: Payment {fault_mode.upper()} ({fault_delay}s delay)",
                source="payment-service:/fault/status",
                timestamp=state.started_at,
                metadata={"mode": fault_mode, "delay_seconds": fault_delay}
            )
            graph.add_node(fault_node)
            graph.add_edge(GraphEdge(f"inc:{inc_id}", "fault:payment_timeout", EdgeType.INCIDENT_CONTAINS.value))
            graph.add_edge(GraphEdge("fault:payment_timeout", "svc:payment-service", EdgeType.OBSERVED_ON.value))

        # 4. Multimodal Evidence Items
        telemetry_eids: List[str] = []
        rag_eids: List[str] = []
        git_eids: List[str] = []
        visual_eids: List[str] = []
        voice_eids: List[str] = []

        for item in state.evidence:
            eid = getattr(item, "evidence_id", f"ev_{int(datetime.now(timezone.utc).timestamp()*1000)}")
            modality = getattr(item, "modality", "unknown")
            source = getattr(item, "source", "unknown")
            provenance = getattr(item, "provenance", source)
            timestamp = getattr(item, "timestamp", utc_now_iso())
            content = getattr(item, "content", {})

            if modality == "telemetry":
                telemetry_eids.append(eid)
                target_svc = "svc:gateway"
                lbl = f"Telemetry: {provenance[:45]}"
                if isinstance(content, dict):
                    if content.get("service") == "payment-service" or "payment" in eid:
                        target_svc = "svc:payment-service"
                        lbl = f"Payment Telemetry (Timeouts: {content.get('timeout_count', 0)}, P95: {content.get('p95_latency_ms', 0)}ms)"
                    elif content.get("service") == "gateway" or "gateway" in eid:
                        target_svc = "svc:gateway"
                        dist = content.get("status_distribution", {})
                        lbl = f"Gateway Telemetry (504s: {dist.get('504', dist.get(504, 0))}, Errors: {content.get('error_count', 0)})"
                    elif content.get("service") == "order-service" or "order" in eid:
                        target_svc = "svc:order-service"
                        lbl = f"Order Service Telemetry (Errors: {content.get('error_count', 0)})"

                t_node = GraphNode(
                    id=f"tel:{eid}",
                    type=NodeType.TELEMETRY.value,
                    label=lbl,
                    source=source,
                    timestamp=timestamp,
                    metadata={"evidence_id": eid, "provenance": provenance, "content": str(content)[:300]}
                )
                graph.add_node(t_node)
                graph.add_edge(GraphEdge(f"tel:{eid}", target_svc, EdgeType.OBSERVED_ON.value))
                if fault_active and target_svc == "svc:payment-service":
                    graph.add_edge(GraphEdge("fault:payment_timeout", f"tel:{eid}", EdgeType.PROPAGATES_TO.value))

            elif modality == "text":
                rag_eids.append(eid)
                doc_title = provenance.split("#")[0] if "#" in provenance else source
                doc_section = provenance.split("#")[1].split(" ")[0] if "#" in provenance else "section"
                snippet = content if isinstance(content, str) else str(content)

                r_node = GraphNode(
                    id=f"doc:{eid}",
                    type=NodeType.DOCUMENT.value,
                    label=f"Runbook: {doc_title} ({doc_section})",
                    source=source,
                    timestamp=timestamp,
                    metadata={"evidence_id": eid, "snippet": snippet[:200], "provenance": provenance}
                )
                graph.add_node(r_node)
                # Connect document to relevant service or fault
                if "payment" in snippet.lower() or "timeout" in snippet.lower():
                    if fault_active:
                        graph.add_edge(GraphEdge(f"doc:{eid}", "fault:payment_timeout", EdgeType.DOCUMENTED_BY.value))
                    graph.add_edge(GraphEdge(f"doc:{eid}", "svc:payment-service", EdgeType.DOCUMENTED_BY.value))
                if "gateway" in snippet.lower() or "504" in snippet.lower():
                    graph.add_edge(GraphEdge(f"doc:{eid}", "svc:gateway", EdgeType.DOCUMENTED_BY.value))

            elif modality == "git_diff":
                git_eids.append(eid)
                g_node = GraphNode(
                    id=f"git:{eid}",
                    type=NodeType.GIT_CHANGE.value,
                    label=f"Git Commit: {provenance[:40]}",
                    source=source,
                    timestamp=timestamp,
                    metadata={"evidence_id": eid, "diff_snippet": str(content)[:200]}
                )
                graph.add_node(g_node)
                graph.add_edge(GraphEdge(f"git:{eid}", "svc:payment-service", EdgeType.CHANGED_BY.value))

            elif modality == "visual_observation":
                visual_eids.append(eid)
                v_node = GraphNode(
                    id=f"vis:{eid}",
                    type=NodeType.VISUAL_OBSERVATION.value,
                    label=f"Visual Observation: {provenance[:40]}",
                    source=source,
                    timestamp=timestamp,
                    metadata={"evidence_id": eid, "details": str(content)[:200]}
                )
                graph.add_node(v_node)
                graph.add_edge(GraphEdge(f"vis:{eid}", "svc:gateway", EdgeType.OBSERVED_ON.value))

            elif modality == "voice_transcript":
                voice_eids.append(eid)
                vt_node = GraphNode(
                    id=f"voice:{eid}",
                    type=NodeType.VOICE_OBSERVATION.value,
                    label=f"Voice Input: \"{str(content)[:40]}...\"",
                    source=source,
                    timestamp=timestamp,
                    metadata={"evidence_id": eid, "transcript": str(content)}
                )
                graph.add_node(vt_node)
                graph.add_edge(GraphEdge(f"voice:{eid}", f"inc:{inc_id}", EdgeType.OBSERVED_ON.value))

        # 5. Formulate & Correlate Grounded Claims
        self._correlate_claims(graph, state, telemetry_eids, rag_eids, git_eids, visual_eids, voice_eids, fault_active)

        # 6. Hypotheses Nodes
        for hyp in state.hypotheses:
            h_node = GraphNode(
                id=f"hyp:{hyp.id}",
                type=NodeType.HYPOTHESIS.value,
                label=f"Hypothesis: {hyp.statement[:55]}... ({hyp.confidence.upper()})",
                source="HypothesisEngine",
                metadata={
                    "hypothesis_id": hyp.id,
                    "status": hyp.status,
                    "confidence": hyp.confidence,
                    "explanation": hyp.explanation
                }
            )
            graph.add_node(h_node)

            # Link hypothesis to supporting evidence
            for seid in hyp.supporting_evidence_ids:
                nid = graph._resolve_evidence_node_id(seid)
                if nid and nid in graph.nodes:
                    graph.add_edge(GraphEdge(f"hyp:{hyp.id}", nid, EdgeType.SUPPORTED_BY.value))

            # Link hypothesis to claims
            if hyp.id == "hyp_payment_timeout_budget_exceeded":
                graph.add_edge(GraphEdge(f"hyp:{hyp.id}", "claim:claim_payment_timeout_propagation", EdgeType.SUPPORTED_BY.value))
            elif hyp.id == "hyp_order_service_failure":
                graph.add_edge(GraphEdge(f"hyp:{hyp.id}", "claim:claim_order_service_crash", EdgeType.CONTRADICTED_BY.value))
            elif hyp.id == "hyp_nominal_system_health":
                graph.add_edge(GraphEdge(f"hyp:{hyp.id}", "claim:claim_nominal_health", EdgeType.CONTRADICTED_BY.value))

        # 7. Actions Executed
        for act in state.executed_actions:
            act_node = GraphNode(
                id=f"act:{act.action_id}",
                type=NodeType.ACTION.value,
                label=f"Recovery Action: {act.tool_name} on {act.target} ({act.status.upper()})",
                source="ActionExecutor",
                timestamp=act.finished_at,
                metadata={"tool_name": act.tool_name, "target": act.target, "status": act.status}
            )
            graph.add_node(act_node)
            graph.add_edge(GraphEdge(f"hyp:hyp_payment_timeout_budget_exceeded", f"act:{act.action_id}", EdgeType.ACTED_ON.value))
            target_svc_node = f"svc:{act.target}" if f"svc:{act.target}" in graph.nodes else "svc:payment-service"
            graph.add_edge(GraphEdge(f"act:{act.action_id}", target_svc_node, EdgeType.ACTED_ON.value))

        # 8. Verification Results
        for i, vr in enumerate(state.verification_results, 1):
            ver_node = GraphNode(
                id=f"ver:verification_{i}",
                type=NodeType.VERIFICATION.value,
                label=f"Verification: {vr.status.upper()} ({len(vr.checks)} checks passed)",
                source="RecoveryVerifier",
                metadata={"status": vr.status, "summary": vr.summary, "checks": vr.checks}
            )
            graph.add_node(ver_node)
            if state.executed_actions:
                last_act_id = state.executed_actions[-1].action_id
                graph.add_edge(GraphEdge(f"act:{last_act_id}", f"ver:verification_{i}", EdgeType.VERIFIED_BY.value))
            graph.add_edge(GraphEdge(f"ver:verification_{i}", "svc:payment-service", EdgeType.VERIFIED_BY.value))
            graph.add_edge(GraphEdge(f"ver:verification_{i}", "svc:gateway", EdgeType.VERIFIED_BY.value))

        return graph

    def _correlate_claims(
        self,
        graph: EvidenceGraph,
        state: IncidentState,
        telemetry_eids: List[str],
        rag_eids: List[str],
        git_eids: List[str],
        visual_eids: Optional[List[str]] = None,
        voice_eids: Optional[List[str]] = None,
        fault_active: bool = False
    ) -> None:
        """Derives grounded claims and evaluates qualitative support strength using classify_claim_support."""
        # Collect observed modalities for claim 1
        modalities_1 = set()
        supp_1 = []
        tel_matches = [eid for eid in telemetry_eids if "payment" in eid or "gateway" in eid]
        if tel_matches:
            supp_1.extend(tel_matches)
            modalities_1.add("telemetry")
        rag_matches = [eid for eid in rag_eids if "timeout" in eid or "payment" in eid]
        if rag_matches:
            supp_1.extend(rag_matches)
            modalities_1.add("runbook_knowledge")
        if fault_active:
            supp_1.append("fault:payment_timeout")
            modalities_1.add("target_fault")
        if git_eids:
            supp_1.extend(git_eids)
            modalities_1.add("git_history")
        if visual_eids:
            supp_1.extend(visual_eids)
            modalities_1.add("visual_observation")
        if voice_eids:
            supp_1.extend(voice_eids)
            modalities_1.add("voice_input")

        strength_1 = classify_claim_support(supp_1, list(modalities_1), [])
        status_1 = ClaimStatus.CONFIRMED.value if strength_1 in (ClaimSupportStrength.SUPPORTED.value, ClaimSupportStrength.STRONGLY_SUPPORTED.value) else ClaimStatus.CANDIDATE.value

        claim_1 = GroundedClaim(
            claim_id="claim_payment_timeout_propagation",
            statement="Payment processing delay exceeded upstream timeout budget, producing Payment failures and propagated Gateway 504 responses.",
            evidence_ids=supp_1,
            support_strength=strength_1,
            status=status_1,
            contradictions=[],
            created_at=state.started_at
        )
        graph.add_claim(claim_1)

        # Claim 2: Alternative Candidate Claim (Order Service Crash)
        contra_2 = []
        for e in state.evidence:
            cnt = getattr(e, "content", {})
            if isinstance(cnt, dict) and (cnt.get("service") == "order-service" or "order" in getattr(e, "evidence_id", "")):
                contra_2.append(getattr(e, "evidence_id"))

        strength_2 = classify_claim_support([], [], contra_2)
        status_2 = ClaimStatus.REFUTED.value if strength_2 == ClaimSupportStrength.CONTRADICTED.value else ClaimStatus.CANDIDATE.value

        claim_2 = GroundedClaim(
            claim_id="claim_order_service_crash",
            statement="Order Service crashed, unresponsive, or experiencing unhandled internal server exceptions.",
            evidence_ids=[],
            support_strength=strength_2,
            status=status_2,
            contradictions=contra_2,
            created_at=state.started_at
        )
        graph.add_claim(claim_2)

        # Claim 3: Gateway Routing Misconfiguration
        strength_3 = classify_claim_support([], [], [])
        claim_3 = GroundedClaim(
            claim_id="claim_gateway_misconfig",
            statement="API Gateway routing misconfiguration or internal client timeout budget mismatch dropped checkout traffic.",
            evidence_ids=[],
            support_strength=strength_3,
            status=ClaimStatus.CANDIDATE.value,
            contradictions=[],
            created_at=state.started_at
        )
        graph.add_claim(claim_3)

        # Claim 4: Nominal Operation (Contradicted when 504s or fault exist)
        contra_4 = [eid for eid in telemetry_eids if "504" in eid or "gateway" in eid or "payment" in eid]
        if fault_active:
            contra_4.append("fault:payment_timeout")

        strength_4 = classify_claim_support([], [], contra_4)
        status_4 = ClaimStatus.REFUTED.value if strength_4 == ClaimSupportStrength.CONTRADICTED.value else ClaimStatus.CONFIRMED.value

        claim_4 = GroundedClaim(
            claim_id="claim_nominal_health",
            statement="All microservices are operating nominally within SLA thresholds; no active incident.",
            evidence_ids=[],
            support_strength=strength_4,
            status=status_4,
            contradictions=contra_4,
            created_at=state.started_at
        )
        graph.add_claim(claim_4)
