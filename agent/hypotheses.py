from typing import Any, Dict, List, Optional
from agent.models import ConfidenceLevel, Hypothesis, HypothesisStatus
from multimodal.schema import MultimodalEvidenceItem

class HypothesisEngine:
    """Generates and evaluates structured hypotheses against empirical SRE evidence."""

    HYPOTHESIS_PAYMENT_TIMEOUT = "hyp_payment_timeout_budget_exceeded"
    HYPOTHESIS_ORDER_CRASH = "hyp_order_service_failure"
    HYPOTHESIS_GATEWAY_MISCONFIG = "hyp_gateway_routing_failure"
    HYPOTHESIS_NOMINAL_OPERATION = "hyp_nominal_system_health"

    def generate_candidate_hypotheses(self) -> List[Hypothesis]:
        """Produces standard structured candidate hypotheses for the three-tier microservice architecture."""
        return [
            Hypothesis(
                id=self.HYPOTHESIS_PAYMENT_TIMEOUT,
                statement=(
                    "Payment processing delay exceeded the upstream timeout budget, "
                    "causing Payment failures and propagated Gateway 504 responses."
                ),
                status=HypothesisStatus.CANDIDATE.value,
                confidence=ConfidenceLevel.LOW.value,
                explanation="Initial candidate for upstream timeout propagation."
            ),
            Hypothesis(
                id=self.HYPOTHESIS_ORDER_CRASH,
                statement="Order Service crashed, unresponsive, or experiencing unhandled internal server errors.",
                status=HypothesisStatus.CANDIDATE.value,
                confidence=ConfidenceLevel.LOW.value,
                explanation="Initial candidate for intermediate tier failure."
            ),
            Hypothesis(
                id=self.HYPOTHESIS_GATEWAY_MISCONFIG,
                statement="API Gateway routing misconfiguration or internal client timeout budget mismatch.",
                status=HypothesisStatus.CANDIDATE.value,
                confidence=ConfidenceLevel.LOW.value,
                explanation="Initial candidate for edge ingress failure."
            ),
            Hypothesis(
                id=self.HYPOTHESIS_NOMINAL_OPERATION,
                statement="All microservices are operating nominally within SLA thresholds; no active incident.",
                status=HypothesisStatus.CANDIDATE.value,
                confidence=ConfidenceLevel.LOW.value,
                explanation="Baseline hypothesis of healthy operating conditions."
            )
        ]

    def evaluate_hypotheses(
        self,
        hypotheses: List[Hypothesis],
        evidence: List[MultimodalEvidenceItem]
    ) -> List[Hypothesis]:
        """Evaluates each candidate hypothesis against empirical evidence, assigning qualitative confidence."""
        # 1. Parse empirical facts from evidence items
        payment_degraded = False
        payment_fault_active = False
        payment_timeouts = 0
        gateway_504_count = 0
        order_healthy = True
        gateway_healthy = True
        all_zero_errors = True

        evidence_by_id: Dict[str, MultimodalEvidenceItem] = {e.evidence_id: e for e in evidence}

        for e in evidence:
            content = e.content

            # Service Health Inspection
            if isinstance(content, dict) and "service_health" in content:
                health_dict = content["service_health"]
                p_health = health_dict.get("payment-service", {})
                o_health = health_dict.get("order-service", {})
                g_health = health_dict.get("gateway", {})

                if p_health.get("status") in ("degraded", "down", "timeout") or p_health.get("details", {}).get("fault_active"):
                    payment_degraded = True
                if o_health.get("status") != "healthy":
                    order_healthy = False
                if g_health.get("status") != "healthy":
                    gateway_healthy = False

            # Fault Status
            if isinstance(content, dict) and content.get("active") is True:
                payment_fault_active = True

            # Telemetry Metrics
            if isinstance(content, dict) and "service" in content:
                svc = content.get("service")
                err_count = content.get("error_count", 0)
                if err_count > 0:
                    all_zero_errors = False
                if svc == "payment-service":
                    payment_timeouts += content.get("timeout_count", 0)
                elif svc == "gateway":
                    dist = content.get("status_distribution", {})
                    gateway_504_count += dist.get("504", 0) + dist.get(504, 0)

        evaluated: List[Hypothesis] = []

        for h in hypotheses:
            supp: List[str] = []
            contra: List[str] = []

            if h.id == self.HYPOTHESIS_PAYMENT_TIMEOUT:
                # Supporting evidence: Payment degraded, fault active, payment timeouts, gateway 504s, runbooks
                for eid, ev in evidence_by_id.items():
                    if ev.modality == "telemetry":
                        meta_type = ev.metadata.get("type", "")
                        if meta_type == "fault_injection_active" and payment_fault_active:
                            supp.append(eid)
                        elif meta_type == "service_health" and payment_degraded:
                            supp.append(eid)
                        elif meta_type == "service_metrics" and (payment_timeouts > 0 or gateway_504_count > 0):
                            supp.append(eid)
                    elif ev.modality == "text" and "payment_timeout" in ev.source:
                        supp.append(eid)

                if payment_fault_active or (payment_degraded and gateway_504_count > 0):
                    h.status = HypothesisStatus.SUPPORTED.value
                    h.confidence = ConfidenceLevel.HIGH.value if payment_fault_active else ConfidenceLevel.MEDIUM.value
                    h.explanation = (
                        "Empirical evidence demonstrates Payment Service degradation/fault correlated with "
                        f"Gateway 504 timeouts ({gateway_504_count} recorded) and matching timeout runbook SLA."
                    )
                elif not payment_degraded and payment_timeouts == 0 and gateway_504_count == 0:
                    h.status = HypothesisStatus.CONTRADICTED.value
                    h.confidence = ConfidenceLevel.LOW.value
                    h.explanation = "No payment timeouts or gateway 504s detected in telemetry."
                else:
                    h.status = HypothesisStatus.UNVERIFIED.value
                    h.confidence = ConfidenceLevel.LOW.value

            elif h.id == self.HYPOTHESIS_ORDER_CRASH:
                # Contradicted if Order Service health is verified 200 OK
                if order_healthy:
                    for eid, ev in evidence_by_id.items():
                        if ev.modality == "telemetry" and ev.metadata.get("type") == "service_health":
                            contra.append(eid)
                    h.status = HypothesisStatus.CONTRADICTED.value
                    h.confidence = ConfidenceLevel.LOW.value
                    h.explanation = "Order Service health endpoint returns healthy (200 OK); service is not crashed."
                else:
                    h.status = HypothesisStatus.SUPPORTED.value
                    h.confidence = ConfidenceLevel.MEDIUM.value
                    h.explanation = "Order Service is reporting degraded or unhealthy state."

            elif h.id == self.HYPOTHESIS_GATEWAY_MISCONFIG:
                if gateway_healthy:
                    for eid, ev in evidence_by_id.items():
                        if ev.modality == "telemetry" and ev.metadata.get("type") == "service_health":
                            contra.append(eid)
                    h.status = HypothesisStatus.CONTRADICTED.value
                    h.confidence = ConfidenceLevel.LOW.value
                    h.explanation = "API Gateway is running and healthy; errors originate from upstream timeout propagation."
                else:
                    h.status = HypothesisStatus.SUPPORTED.value
                    h.confidence = ConfidenceLevel.MEDIUM.value

            elif h.id == self.HYPOTHESIS_NOMINAL_OPERATION:
                if payment_degraded or payment_fault_active or gateway_504_count > 0 or not all_zero_errors:
                    for eid, ev in evidence_by_id.items():
                        if ev.modality == "telemetry":
                            contra.append(eid)
                    h.status = HypothesisStatus.CONTRADICTED.value
                    h.confidence = ConfidenceLevel.LOW.value
                    h.explanation = f"Active incident signals observed: errors={not all_zero_errors}, 504s={gateway_504_count}, payment_fault={payment_fault_active}."
                else:
                    h.status = HypothesisStatus.SUPPORTED.value
                    h.confidence = ConfidenceLevel.HIGH.value
                    h.explanation = "All service health checks pass and error counts are zero."

            h.supporting_evidence_ids = supp
            h.contradicting_evidence_ids = contra
            evaluated.append(h)

        return evaluated

    def select_primary_hypothesis(self, evaluated: List[Hypothesis]) -> Optional[Hypothesis]:
        """Selects the highest-confidence supported hypothesis."""
        supported = [h for h in evaluated if h.status == HypothesisStatus.SUPPORTED.value]
        if not supported:
            return None

        # Sort by confidence level: high > medium > low, then by supporting evidence count
        conf_rank = {ConfidenceLevel.HIGH.value: 3, ConfidenceLevel.MEDIUM.value: 2, ConfidenceLevel.LOW.value: 1}
        supported.sort(
            key=lambda h: (conf_rank.get(h.confidence, 0), len(h.supporting_evidence_ids)),
            reverse=True
        )
        return supported[0]
