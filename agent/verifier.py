import time
from typing import Any, Dict, List, Optional

from agent.models import VerificationResult, VerificationStatus
from sandbox.process_sandbox import ProcessTargetEnvironment
from telemetry.db import get_all_summaries
from telemetry.snapshot import get_fault_status
from telemetry.traffic import generate_traffic

class RecoveryVerifier:
    """Performs empirical verification of service recovery, comparing before and after states."""

    def __init__(self, env: Optional[ProcessTargetEnvironment] = None):
        self.env = env or ProcessTargetEnvironment()

    def verify_recovery(
        self,
        before_snapshot: Dict[str, Any],
        wait_seconds: float = 1.0,
        probe_count: int = 4
    ) -> VerificationResult:
        """Executes full empirical verification probe and before/after comparison."""
        # 1. Wait for services to stabilize
        if wait_seconds > 0:
            time.sleep(wait_seconds)

        checks: List[Dict[str, Any]] = []

        # 2. Probe live health endpoints
        after_health = self.env.check_all_health()
        all_healthy = all(h.get("status") == "healthy" for h in after_health.values())
        checks.append({
            "check": "service_health_check",
            "passed": all_healthy,
            "details": {svc: h.get("status") for svc, h in after_health.items()}
        })

        # 3. Probe Payment fault endpoint
        after_fault = get_fault_status()
        fault_cleared = (after_fault.get("active") is False)
        checks.append({
            "check": "fault_state_cleared",
            "passed": fault_cleared,
            "details": after_fault
        })

        # 4. Probe end-to-end checkout traffic
        try:
            probe_result = generate_traffic(
                num_requests=probe_count,
                concurrency=1,
                interval_seconds=0.03
            )
            probe_success = (probe_result.get("failed_requests", 0) == 0 and probe_result.get("successful_requests", 0) > 0)
            avg_latency = probe_result.get("avg_latency_ms", 9999.0)
            within_sla = (avg_latency < 1200.0)  # Standard SLA threshold
            checks.append({
                "check": "probe_checkout_traffic",
                "passed": probe_success,
                "details": {
                    "total": probe_result.get("total_requests"),
                    "successful": probe_result.get("successful_requests"),
                    "failed": probe_result.get("failed_requests"),
                    "avg_latency_ms": avg_latency
                }
            })
            checks.append({
                "check": "latency_within_sla",
                "passed": within_sla,
                "details": f"Average latency {avg_latency}ms (budget < 1200ms)"
            })
        except Exception as e:
            probe_success = False
            within_sla = False
            checks.append({
                "check": "probe_checkout_traffic",
                "passed": False,
                "details": f"Probe exception: {e}"
            })

        # 5. Build before and after states
        before_state = {
            "overall_status": before_snapshot.get("overall_status"),
            "payment_health": before_snapshot.get("service_health", {}).get("payment-service", {}).get("status"),
            "fault_active": before_snapshot.get("fault_status", {}).get("active", False),
            "gateway_error_rate": before_snapshot.get("metrics_summary", {}).get("gateway", {}).get("error_rate", 0.0),
            "gateway_timeouts": before_snapshot.get("metrics_summary", {}).get("gateway", {}).get("timeout_count", 0)
        }

        after_state = {
            "overall_status": "healthy" if all_healthy else "degraded",
            "payment_health": after_health.get("payment-service", {}).get("status"),
            "fault_active": after_fault.get("active", False),
            "probe_successful_requests": probe_result.get("successful_requests") if "probe_result" in locals() else 0,
            "probe_failed_requests": probe_result.get("failed_requests") if "probe_result" in locals() else probe_count,
            "probe_avg_latency_ms": avg_latency if "avg_latency" in locals() else None
        }

        # 6. Synthesize verification status
        if all_healthy and fault_cleared and probe_success and within_sla:
            status = VerificationStatus.VERIFIED_RECOVERED.value
            summary = (
                f"Empirical verification confirmed complete recovery: all services healthy (200 OK), "
                f"fault cleared, probe traffic 100% successful ({probe_count}/{probe_count}), "
                f"and average latency ({avg_latency}ms) well within upstream SLA."
            )
        elif all_healthy and not probe_success:
            status = VerificationStatus.PARTIALLY_RECOVERED.value
            summary = "Health endpoints return healthy, but live probe traffic experienced errors."
        elif not all_healthy and not probe_success:
            status = VerificationStatus.STILL_FAILING.value
            summary = "Services remain in degraded state and probe traffic continues to fail."
        else:
            status = VerificationStatus.VERIFICATION_FAILED.value
            summary = "Verification failed to demonstrate reliable recovery."

        return VerificationResult(
            status=status,
            checks=checks,
            before_state=before_state,
            after_state=after_state,
            evidence_ids=["verification_probe_telemetry", "verification_health_check"],
            summary=summary
        )
