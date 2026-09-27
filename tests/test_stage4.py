import io
import json
import os
from pathlib import Path
import pytest
from PIL import Image

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
from services.config import LOGS_DIR

# ---------------------------------------------------------------------------
# 1. Voice Module Imports & Interfaces
# ---------------------------------------------------------------------------

def test_voice_module_imports_correctly():
    """Verify voice transcription and synthesis modules import and expose required interfaces."""
    transcriber = VoiceTranscriber()
    assert hasattr(transcriber, "transcribe_file")
    assert hasattr(transcriber, "transcribe_bytes")

    synthesizer = VoiceSynthesizer()
    assert hasattr(synthesizer, "synthesize")

    assert callable(transcribe_audio)
    assert callable(synthesize_speech)

# ---------------------------------------------------------------------------
# 2. Missing & Invalid Audio Handling
# ---------------------------------------------------------------------------

def test_missing_and_invalid_audio_handled_safely(tmp_path):
    """Verify missing, empty, or corrupt audio inputs are safely handled without throwing uncaught exceptions."""
    transcriber = VoiceTranscriber()

    # Non-existent file
    missing_path = tmp_path / "non_existent_audio.wav"
    res1 = transcriber.transcribe_file(missing_path)
    assert res1.status == "error"
    assert res1.transcript == ""
    assert res1.confidence is None
    assert "not found" in res1.error.lower()

    # Empty 0-byte file
    empty_path = tmp_path / "empty_audio.wav"
    empty_path.write_bytes(b"")
    res2 = transcriber.transcribe_file(empty_path)
    assert res2.status == "error"
    assert res2.transcript == ""
    assert res2.confidence is None
    assert "empty" in res2.error.lower()

    # Empty bytes
    res3 = transcriber.transcribe_bytes(b"")
    assert res3.status == "error"
    assert res3.transcript == ""
    assert res3.confidence is None
    assert "empty" in res3.error.lower()

    # Corrupt / random non-audio bytes
    corrupt_bytes = b"NOT_VALID_AUDIO_HEADER_DATA_1234567890"
    res4 = transcriber.transcribe_bytes(corrupt_bytes, audio_format="wav")
    assert res4.status == "error"
    assert res4.transcript == ""
    assert res4.confidence is None
    assert res4.error is not None

# ---------------------------------------------------------------------------
# 3. STT Unavailable State
# ---------------------------------------------------------------------------

def test_stt_unavailable_state_represented_correctly(tmp_path):
    """Verify that when an unavailable STT backend is selected, structured unavailable status is returned."""
    transcriber = VoiceTranscriber(backend="offline_unconfigured_backend")
    sample_file = tmp_path / "test.wav"
    sample_file.write_bytes(b"sample_data")

    res = transcriber.transcribe_file(sample_file)
    assert res.status == "unavailable"
    assert res.transcript == ""
    assert res.confidence is None
    assert res.backend == "offline_unconfigured_backend"
    assert "unavailable" in res.error.lower()

# ---------------------------------------------------------------------------
# 4. No Fake Transcripts Or Hallucinated Confidence
# ---------------------------------------------------------------------------

def test_no_fake_transcript_produced():
    """Verify that failed transcription never fabricates text or confidence values."""
    transcriber = VoiceTranscriber()
    res = transcriber.transcribe_bytes(b"invalid_bytes_xyz")

    # Transcript MUST be empty
    assert res.transcript == ""
    # Confidence MUST be None (null), never a fabricated float
    assert res.confidence is None
    assert res.status == "error"

# ---------------------------------------------------------------------------
# 5. TTS Module Imports & Interfaces
# ---------------------------------------------------------------------------

def test_tts_module_imports_correctly():
    """Verify TTS synthesizer can be initialized with default and custom settings."""
    synth_default = VoiceSynthesizer()
    assert synth_default.backend == "gtts"
    assert synth_default.language == "en"

    synth_custom = VoiceSynthesizer(backend="gtts", language="en")
    assert synth_custom.language == "en"

# ---------------------------------------------------------------------------
# 6. TTS Failure Handling
# ---------------------------------------------------------------------------

def test_tts_failure_is_represented_correctly(tmp_path):
    """Verify TTS failures for empty text and unsupported backends return structured error objects."""
    synthesizer = VoiceSynthesizer()

    # Empty string
    res_empty = synthesizer.synthesize("")
    assert res_empty.status == "error"
    assert res_empty.output_audio_path is None
    assert res_empty.audio_bytes_length == 0
    assert "empty" in res_empty.error.lower()

    # Whitespace only
    res_ws = synthesizer.synthesize("   \n\t  ")
    assert res_ws.status == "error"
    assert res_ws.output_audio_path is None
    assert "empty" in res_ws.error.lower()

    # Unsupported backend
    synth_bad = VoiceSynthesizer(backend="nonexistent_tts_backend")
    res_bad = synth_bad.synthesize("test sentence")
    assert res_bad.status == "unavailable"
    assert res_bad.output_audio_path is None
    assert "not supported" in res_bad.error.lower()

# ---------------------------------------------------------------------------
# 7. Real Audio Synthesis & Transcription Roundtrip
# ---------------------------------------------------------------------------

def test_real_voice_synthesis_and_transcription(tmp_path):
    """Verify real speech synthesis creates a valid audio artifact, and STT transcribes it."""
    out_audio = tmp_path / "payment_timeout_alert.mp3"
    phrase = "payment service timeout error"

    synth_res = synthesize_speech(phrase, output_path=out_audio)
    assert synth_res.status == "success", f"Synthesis failed: {synth_res.error}"
    assert synth_res.output_audio_path is not None
    assert Path(synth_res.output_audio_path).exists()
    assert synth_res.audio_bytes_length > 1000

    # Transcribe the real synthesized audio artifact
    trans_res = transcribe_audio(synth_res.output_audio_path)
    assert trans_res.status == "success", f"Transcription failed: {trans_res.error}"
    assert len(trans_res.transcript) > 0
    # Must capture genuine audio content
    assert "timeout" in trans_res.transcript.lower() or "payment" in trans_res.transcript.lower()
    # Confidence is either a real float or None (null) - never a fabricated constant
    if trans_res.confidence is not None:
        assert isinstance(trans_res.confidence, (int, float))
        assert 0.0 <= trans_res.confidence <= 1.0

# ---------------------------------------------------------------------------
# 8. Image Validation Works
# ---------------------------------------------------------------------------

def test_image_validation_works(tmp_path):
    """Verify Image validator accurately validates real images created via PIL."""
    inspector = VisionInspector()

    # Create real PNG image
    test_img = Image.new("RGB", (320, 240), color=(41, 128, 185))
    img_path = tmp_path / "valid_test_img.png"
    test_img.save(img_path)

    is_valid, err, img = inspector.validate_image(img_path)
    assert is_valid is True
    assert err is None
    assert img is not None
    assert img.width == 320
    assert img.height == 240

    # Test bytes input validation
    with open(img_path, "rb") as f:
        img_bytes = f.read()
    b_valid, b_err, b_img = inspector.validate_image(img_bytes)
    assert b_valid is True
    assert b_img.width == 320

# ---------------------------------------------------------------------------
# 9. Invalid Image Input Rejected Safely
# ---------------------------------------------------------------------------

def test_invalid_image_input_rejected_safely(tmp_path):
    """Verify non-existent, empty, and corrupt image files are safely rejected."""
    inspector = VisionInspector()

    # Missing file
    missing_file = tmp_path / "non_existent_img.png"
    res1 = inspector.inspect(missing_file)
    assert res1.status == "error"
    assert "not found" in res1.error.lower()
    assert len(res1.observations) == 0

    # 0-byte file
    empty_file = tmp_path / "empty_img.png"
    empty_file.write_bytes(b"")
    res2 = inspector.inspect(empty_file)
    assert res2.status == "error"
    assert "empty" in res2.error.lower()
    assert len(res2.observations) == 0

    # Corrupt non-image file
    corrupt_file = tmp_path / "corrupt_file.png"
    corrupt_file.write_text("This is not a PNG or JPEG file at all!", encoding="utf-8")
    res3 = inspector.inspect(corrupt_file)
    assert res3.status == "error"
    assert "invalid image format" in res3.error.lower() or "corrupt" in res3.error.lower()
    assert len(res3.observations) == 0

# ---------------------------------------------------------------------------
# 10. Vision Backend Discovery & Unavailable State
# ---------------------------------------------------------------------------

def test_vision_backend_discovery_and_unavailable_state(tmp_path):
    """Verify vision backend discovery accurately detects credential availability and represents unavailable state without fabrication."""
    discovery = discover_vision_backend()
    assert "backend" in discovery
    assert "available" in discovery
    assert "local_capabilities" in discovery

    # When no API key is present, default backend must be 'none' and available=False
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        assert discovery["available"] is False
        assert discovery["backend"] == "none"

        # Create a real image
        img_path = tmp_path / "test_diagram.png"
        img = Image.new("RGB", (400, 300), color=(255, 255, 255))
        img.save(img_path)

        inspector = VisionInspector()
        res = inspector.inspect(img_path)
        assert res.status == "unavailable"
        assert res.backend == "none"
        # MUST NOT fabricate observations
        assert len(res.observations) == 0
        assert "unavailable" in res.error.lower()

    # Local structural inspector should extract real verified image properties
    structural_inspector = VisionInspector(backend="local_structural")
    img_path = tmp_path / "structural_test.png"
    img = Image.new("RGB", (640, 480), color=(50, 100, 150))
    img.save(img_path)

    struct_res = structural_inspector.inspect(img_path)
    assert struct_res.status == "success"
    assert struct_res.backend == "local_structural"
    assert len(struct_res.observations) >= 2
    assert "640x480" in struct_res.observations[0]
    assert struct_res.metadata["width"] == 640
    assert struct_res.metadata["height"] == 480
    assert struct_res.metadata["aspect_ratio"] == 1.33

# ---------------------------------------------------------------------------
# 11. Multimodal Evidence Schema & Provenance Preservation
# ---------------------------------------------------------------------------

def test_evidence_objects_preserve_modality_source_provenance():
    """Verify MultimodalEvidenceItem and Bundle preserve modality, source, provenance, and citations."""
    bundle = MultimodalEvidenceBundle(
        bundle_id="bundle_test_001",
        timestamp="2026-09-27T18:00:00Z",
        query="payment timeout 504"
    )

    # 1. Text evidence item
    text_item = MultimodalEvidenceItem(
        evidence_id="text_001",
        modality="text",
        source="knowledge/runbooks/payment_timeout.md",
        timestamp="2026-09-27T18:00:00Z",
        content="Order Service to Payment Service Client Timeout: 2.0s",
        provenance="knowledge/runbooks/payment_timeout.md#Timeout Budget",
        status="verified",
        metadata={"section": "Timeout Budget", "score": 0.85}
    )
    bundle.add_item(text_item)

    # 2. Voice transcript item
    transcript = VoiceTranscript(
        transcript="payment service has timed out",
        source="microphone",
        confidence=None,
        timestamp="2026-09-27T18:01:00Z",
        audio_format="wav",
        status="success",
        backend="google_web_speech"
    )
    voice_item = MultimodalEvidenceBundle.from_voice_transcript(transcript)
    bundle.add_item(voice_item)

    # 3. Visual inspection item
    inspection = VisualInspectionResult(
        image_id="img_101",
        source="logs/visualizations/topology_diagram.png",
        timestamp="2026-09-27T18:02:00Z",
        observations=["Image format: PNG, Resolution: 1488x885, Color mode: RGBA", "Aspect ratio: 1.68"],
        status="success",
        backend="local_structural",
        metadata={"width": 1488, "height": 885}
    )
    vision_item = MultimodalEvidenceBundle.from_visual_inspection(inspection)
    bundle.add_item(vision_item)

    # 4. Telemetry item
    snapshot_data = {
        "overall_status": "incident_active",
        "timestamp_iso": "2026-09-27T18:03:00Z",
        "service_health": {"payment-service": {"status": "degraded"}},
        "metrics_summary": {"payment-service": {"error_count": 5}}
    }
    telemetry_item = MultimodalEvidenceBundle.from_telemetry_snapshot(snapshot_data, "logs/snapshots/snapshot_100.json")
    bundle.add_item(telemetry_item)

    # Assertions on bundle
    assert len(bundle.items) == 4

    text_filtered = bundle.filter_by_modality("text")
    assert len(text_filtered) == 1
    assert text_filtered[0].provenance == "knowledge/runbooks/payment_timeout.md#Timeout Budget"

    voice_filtered = bundle.filter_by_modality("voice_transcript")
    assert len(voice_filtered) == 1
    assert voice_filtered[0].content == "payment service has timed out"
    assert voice_filtered[0].metadata["confidence"] is None  # no fake confidence!

    vision_filtered = bundle.filter_by_modality("visual_observation")
    assert len(vision_filtered) == 1
    assert vision_filtered[0].metadata["width"] == 1488

    telemetry_filtered = bundle.filter_by_modality("telemetry")
    assert len(telemetry_filtered) == 1
    assert telemetry_filtered[0].content["overall_status"] == "incident_active"

    # Verify JSON serialization works without error
    bundle_dict = bundle.to_dict()
    assert bundle_dict["bundle_id"] == "bundle_test_001"
    json_str = json.dumps(bundle_dict)
    assert len(json_str) > 0

# ---------------------------------------------------------------------------
# 12. Generated Visualizations Use Real Project Data
# ---------------------------------------------------------------------------

def test_generated_topology_and_telemetry_visualizations(tmp_path):
    """Verify generated topology and telemetry diagrams create real valid PNG files based on actual configuration and SQLite database."""
    top_file = tmp_path / "test_topology.png"
    generated_top = generate_topology_diagram(output_path=top_file)
    assert Path(generated_top).exists()
    assert Path(generated_top).stat().st_size > 1000

    # Verify PNG integrity with PIL
    top_img = Image.open(generated_top)
    assert top_img.format == "PNG"
    assert top_img.width >= 800
    assert top_img.height >= 500

    tel_file = tmp_path / "test_telemetry.png"
    generated_tel = generate_telemetry_chart(output_path=tel_file)
    assert Path(generated_tel).exists()
    assert Path(generated_tel).stat().st_size > 1000

    tel_img = Image.open(generated_tel)
    assert tel_img.format == "PNG"
    assert tel_img.width >= 800
    assert tel_img.height >= 500
