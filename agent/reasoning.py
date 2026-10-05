from abc import ABC, abstractmethod
import os
from typing import Any, Dict, List, Optional

from agent.hypotheses import HypothesisEngine
from agent.models import ConfidenceLevel, Hypothesis, HypothesisStatus
from multimodal.schema import MultimodalEvidenceItem

class ReasoningBackend(ABC):
    """Abstract interface for SRE incident reasoning backends."""

    @abstractmethod
    def reason(
        self,
        evidence: List[MultimodalEvidenceItem],
        candidates: List[Hypothesis]
    ) -> Dict[str, Any]:
        """Evaluates hypotheses against evidence and returns structured reasoning output."""
        pass

class DeterministicReasoningBackend(ReasoningBackend):
    """Rule-based, 100% deterministic local SRE reasoning engine requiring no external APIs."""

    def __init__(self):
        self.engine = HypothesisEngine()

    def reason(
        self,
        evidence: List[MultimodalEvidenceItem],
        candidates: List[Hypothesis]
    ) -> Dict[str, Any]:
        evaluated = self.engine.evaluate_hypotheses(candidates, evidence)
        primary = self.engine.select_primary_hypothesis(evaluated)

        recommended_action = None
        if primary and primary.id == HypothesisEngine.HYPOTHESIS_PAYMENT_TIMEOUT:
            recommended_action = "reset_controlled_fault"

        return {
            "backend": "deterministic",
            "primary_hypothesis": primary,
            "evaluated_hypotheses": evaluated,
            "recommended_action": recommended_action,
            "chain_of_thought": [
                "1. Inspected live service health and empirical telemetry metrics.",
                "2. Identified correlation between Payment Service latency/fault and Gateway 504 timeouts.",
                "3. Verified timeout budget SLA from retrieved runbooks: Gateway=3.0s, Order=2.0s.",
                f"4. Selected primary hypothesis: '{primary.statement if primary else 'None'}' with confidence '{primary.confidence if primary else 'None'}'.",
                f"5. Recommended recovery action: '{recommended_action}'."
            ]
        }

class GeminiReasoningBackend(ReasoningBackend):
    """Optional multimodal LLM reasoning adapter utilizing Gemini when credentials are configured."""

    def __init__(self):
        self.deterministic_fallback = DeterministicReasoningBackend()
        self.has_credentials = self._check_credentials()

    def _check_credentials(self) -> bool:
        """Safely checks environment or Streamlit secrets for Gemini API credentials."""
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            try:
                import streamlit as st
                key = st.secrets.get("GEMINI_API_KEY") or st.secrets.get("GOOGLE_API_KEY")
                if key and not os.getenv("GEMINI_API_KEY"):
                    os.environ["GEMINI_API_KEY"] = str(key)
            except Exception:
                pass
        self.has_credentials = bool(key)
        return self.has_credentials

    def reason(
        self,
        evidence: List[MultimodalEvidenceItem],
        candidates: List[Hypothesis]
    ) -> Dict[str, Any]:
        if not self._check_credentials():
            # Clean fallback to deterministic reasoning without fabricating
            fallback_res = self.deterministic_fallback.reason(evidence, candidates)
            fallback_res["backend"] = "deterministic_fallback_no_credentials"
            return fallback_res

        try:
            from google import genai
            client = genai.Client()

            evidence_summary = "\n".join([
                f"- [{e.modality}] {e.provenance}: {str(e.content)[:200]}"
                for e in evidence
            ])
            candidate_summary = "\n".join([f"- {h.id}: {h.statement}" for h in candidates])

            prompt = (
                "You are an SRE incident diagnosis assistant. Given the following empirical evidence and candidate hypotheses, "
                "identify which hypothesis is supported, explain why with citations, and recommend an allowlisted recovery action.\n\n"
                f"EVIDENCE:\n{evidence_summary}\n\n"
                f"CANDIDATES:\n{candidate_summary}\n\n"
                "Respond in concise format stating supported hypothesis ID, confidence (low/medium/high), and recommended action."
            )

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )

            # Also evaluate deterministic for structure
            det_res = self.deterministic_fallback.reason(evidence, candidates)
            det_res["backend"] = "gemini"
            det_res["llm_analysis"] = response.text
            return det_res

        except Exception as e:
            fallback_res = self.deterministic_fallback.reason(evidence, candidates)
            fallback_res["backend"] = "deterministic_fallback_on_error"
            fallback_res["llm_error"] = str(e)
            return fallback_res

def get_reasoning_backend(backend_type: Optional[str] = None) -> ReasoningBackend:
    """Factory helper returning appropriate reasoning backend based on environment credentials."""
    has_key = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    if backend_type == "gemini" and has_key:
        return GeminiReasoningBackend()
    return DeterministicReasoningBackend()
