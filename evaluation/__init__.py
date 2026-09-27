"""ChaosPilot Evaluation Framework

Empirical evaluation and benchmarking of retrieval, claim grounding,
agent reasoning, action safety, and verification correctness.
"""

from evaluation.metrics import (
    recall_at_k,
    precision_at_k,
    mean_reciprocal_rank,
    evidence_coverage,
    unsupported_claim_count,
    contradicted_claim_count,
    evidence_trace_completeness,
    correct_hypothesis_selected,
    safe_action_selected,
    verification_success,
    false_resolution_count,
    unsafe_action_count
)
from evaluation.benchmarks import SyntheticControlledBenchmarkFixture, get_default_benchmark_fixture
from evaluation.framework import SystemEvaluator, BenchmarkComparisonResult

__all__ = [
    "recall_at_k",
    "precision_at_k",
    "mean_reciprocal_rank",
    "evidence_coverage",
    "unsupported_claim_count",
    "contradicted_claim_count",
    "evidence_trace_completeness",
    "correct_hypothesis_selected",
    "safe_action_selected",
    "verification_success",
    "false_resolution_count",
    "unsafe_action_count",
    "SyntheticControlledBenchmarkFixture",
    "get_default_benchmark_fixture",
    "SystemEvaluator",
    "BenchmarkComparisonResult"
]
