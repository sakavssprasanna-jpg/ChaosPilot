from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

def utc_now_iso() -> str:
    """Returns current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()

@dataclass
class VoiceTranscript:
    """Structured representation of speech-to-text transcription."""
    transcript: str
    source: str
    confidence: Optional[float]
    timestamp: str
    audio_format: str
    status: str  # 'success', 'unintelligible', 'unavailable', 'error'
    backend: str = "google_web_speech"
    duration_seconds: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class VoiceSynthesisResult:
    """Structured representation of text-to-speech audio synthesis."""
    text: str
    output_audio_path: Optional[str]
    timestamp: str
    status: str  # 'success', 'unavailable', 'error'
    backend: str = "gtts"
    audio_bytes_length: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class VisualInspectionResult:
    """Structured representation of visual evidence analysis."""
    image_id: str
    source: str
    timestamp: str
    observations: List[str] = field(default_factory=list)
    status: str = "success"  # 'success', 'unavailable', 'error'
    backend: str = "none"  # 'gemini', 'local_structural', 'none'
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class MultimodalEvidenceItem:
    """Generic evidence container preserving modality and provenance."""
    evidence_id: str
    modality: str  # 'text', 'voice_transcript', 'visual_observation', 'telemetry', 'git_diff'
    source: str
    timestamp: str
    content: Any
    provenance: str
    status: str = "verified"  # 'verified', 'unverified', 'unavailable', 'error'
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class MultimodalEvidenceBundle:
    """Multi-evidence bundle coordinating text, voice, vision, telemetry, and git evidence."""
    bundle_id: str
    timestamp: str
    query: Optional[str] = None
    items: List[MultimodalEvidenceItem] = field(default_factory=list)

    def add_item(self, item: MultimodalEvidenceItem) -> None:
        self.items.append(item)

    def filter_by_modality(self, modality: str) -> List[MultimodalEvidenceItem]:
        return [item for item in self.items if item.modality == modality]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "timestamp": self.timestamp,
            "query": self.query,
            "items": [item.to_dict() for item in self.items]
        }

    @classmethod
    def from_voice_transcript(cls, transcript: VoiceTranscript) -> MultimodalEvidenceItem:
        return MultimodalEvidenceItem(
            evidence_id=f"voice_{int(datetime.now(timezone.utc).timestamp()*1000)}",
            modality="voice_transcript",
            source=transcript.source,
            timestamp=transcript.timestamp,
            content=transcript.transcript,
            provenance=f"audio:{transcript.source} (backend={transcript.backend})",
            status="verified" if transcript.status == "success" else transcript.status,
            metadata={
                "confidence": transcript.confidence,
                "audio_format": transcript.audio_format,
                "duration_seconds": transcript.duration_seconds,
                "error": transcript.error,
                "backend": transcript.backend
            }
        )

    @classmethod
    def from_visual_inspection(cls, inspection: VisualInspectionResult) -> MultimodalEvidenceItem:
        return MultimodalEvidenceItem(
            evidence_id=f"vision_{inspection.image_id}",
            modality="visual_observation",
            source=inspection.source,
            timestamp=inspection.timestamp,
            content=inspection.observations,
            provenance=f"image:{inspection.source} (backend={inspection.backend})",
            status="verified" if inspection.status == "success" else inspection.status,
            metadata={
                "backend": inspection.backend,
                "error": inspection.error,
                **inspection.metadata
            }
        )

    @classmethod
    def from_document_citation(cls, citation: Any) -> MultimodalEvidenceItem:
        doc = getattr(citation, "document", "knowledge_doc")
        sec = getattr(citation, "section", "section")
        chunk_id = getattr(citation, "chunk_id", "chunk")
        score = getattr(citation, "score", 0.0)
        snippet = getattr(citation, "text_snippet", "")
        return MultimodalEvidenceItem(
            evidence_id=f"text_{chunk_id}",
            modality="text",
            source=doc,
            timestamp=utc_now_iso(),
            content=snippet,
            provenance=f"{doc}#{sec} (chunk={chunk_id})",
            status="verified",
            metadata={"score": score, "section": sec, "source_type": getattr(citation, "source_type", "doc")}
        )

    @classmethod
    def from_git_citation(cls, citation: Any) -> MultimodalEvidenceItem:
        commit = getattr(citation, "commit", "unknown")
        author = getattr(citation, "author", "unknown")
        relevance = getattr(citation, "relevance", 0.0)
        message = getattr(citation, "message", "")
        diff = getattr(citation, "diff", "")
        return MultimodalEvidenceItem(
            evidence_id=f"git_{commit[:8]}",
            modality="git_diff",
            source=f"git_commit:{commit[:8]}",
            timestamp=getattr(citation, "date", utc_now_iso()),
            content=f"Message: {message}\n\nDiff:\n{diff}",
            provenance=f"git_commit:{commit} by {author}",
            status="verified",
            metadata={"commit": commit, "author": author, "relevance": relevance}
        )

    @classmethod
    def from_telemetry_snapshot(cls, snapshot: Dict[str, Any], snapshot_file: str = "snapshot.json") -> MultimodalEvidenceItem:
        snap_id = f"telemetry_{int(datetime.now(timezone.utc).timestamp())}"
        summary = snapshot.get("metrics_summary", {})
        health = snapshot.get("service_health", {})
        status = snapshot.get("overall_status", "unknown")
        return MultimodalEvidenceItem(
            evidence_id=snap_id,
            modality="telemetry",
            source=snapshot_file,
            timestamp=snapshot.get("timestamp_iso", utc_now_iso()),
            content={"overall_status": status, "metrics_summary": summary, "service_health": health},
            provenance=f"snapshot:{snapshot_file}",
            status="verified",
            metadata={"overall_status": status, "fault_status": snapshot.get("fault_status", {})}
        )

