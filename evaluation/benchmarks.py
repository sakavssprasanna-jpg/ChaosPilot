from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class SyntheticControlledBenchmarkFixture:
    """Explicit, ground-truth evaluation benchmark fixture.

    DISCLOSURE: This fixture is derived directly from the real, controlled local
    three-tier microservice sandbox incident (Payment timeout propagation).
    It is NOT an external production dataset.
    """
    benchmark_id: str
    label: str
    description: str
    source_environment: str
    incident_type: str
    degraded_service: str
    symptom_service: str
    symptom_http_status: int
    query: str
    ground_truth_relevant_keywords: List[str]
    ground_truth_hypothesis_id: str
    ground_truth_remediation_tool: str
    ground_truth_target: str
    expected_verification_status: str
    expected_final_status: str
    is_synthetic_controlled: bool = True
    limitations: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_default_benchmark_fixture() -> SyntheticControlledBenchmarkFixture:
    """Returns the standardized, ground-truth benchmark fixture for ChaosPilot."""
    return SyntheticControlledBenchmarkFixture(
        benchmark_id="benchmark_payment_timeout_propagation",
        label="SYNTHETIC_CONTROLLED_BENCHMARK",
        description="Controlled Payment Service timeout fault exceeding upstream budget and propagating Gateway 504",
        source_environment="ProcessTargetEnvironment (Gateway :8000 -> Order :8001 -> Payment :8002)",
        incident_type="timeout_propagation",
        degraded_service="payment-service",
        symptom_service="gateway",
        symptom_http_status=504,
        query="payment service timeout 504 gateway artificial delay",
        ground_truth_relevant_keywords=[
            "payment_timeout.md",
            "timeout budget",
            "payment service",
            "client timeout"
        ],
        ground_truth_hypothesis_id="hyp_payment_timeout_budget_exceeded",
        ground_truth_remediation_tool="reset_controlled_fault",
        ground_truth_target="payment-service",
        expected_verification_status="verified_recovered",
        expected_final_status="resolved",
        is_synthetic_controlled=True,
        limitations=(
            "Controlled microservice sandbox simulation evaluated on 1 deterministic "
            "incident topology (Gateway -> Order -> Payment). Not external production traffic."
        ),
        metadata={
            "gateway_timeout_budget_seconds": 3.0,
            "order_timeout_budget_seconds": 2.0,
            "injected_payment_delay_seconds": 4.5
        }
    )
