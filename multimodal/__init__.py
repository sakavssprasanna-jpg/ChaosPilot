"""ChaosPilot Multimodal Perception Layer (Stage 4)

Provides perception capabilities for voice and vision evidence:
- Voice transcription (Speech-to-Text)
- Voice synthesis (Text-to-Speech)
- Vision inspection and backend discovery
- Topology and telemetry diagram visualizers
- Unified multimodal evidence schema
"""

from multimodal.schema import (
    VoiceTranscript,
    VoiceSynthesisResult,
    VisualInspectionResult,
    MultimodalEvidenceItem,
    MultimodalEvidenceBundle
)
from multimodal.voice_transcriber import VoiceTranscriber, transcribe_audio
from multimodal.voice_synthesizer import VoiceSynthesizer, synthesize_speech
from multimodal.vision_inspector import VisionInspector, discover_vision_backend
from multimodal.visualizer import generate_topology_diagram, generate_telemetry_chart

__all__ = [
    "VoiceTranscript",
    "VoiceSynthesisResult",
    "VisualInspectionResult",
    "MultimodalEvidenceItem",
    "MultimodalEvidenceBundle",
    "VoiceTranscriber",
    "transcribe_audio",
    "VoiceSynthesizer",
    "synthesize_speech",
    "VisionInspector",
    "discover_vision_backend",
    "generate_topology_diagram",
    "generate_telemetry_chart"
]
