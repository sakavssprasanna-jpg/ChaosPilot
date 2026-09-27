import io
import os
from pathlib import Path
import time
from typing import Any, Dict, Optional, Union

import static_ffmpeg
# Ensure static ffmpeg binaries are in PATH before pydub initializes
try:
    static_ffmpeg.add_paths()
except Exception:
    pass

import speech_recognition as sr
from pydub import AudioSegment

from multimodal.schema import VoiceTranscript, utc_now_iso

SUPPORTED_BACKENDS = ["google_web_speech"]

class VoiceTranscriber:
    """Reusable Speech-to-Text transcription utility supporting multi-format audio."""

    def __init__(self, backend: str = "google_web_speech", timeout: float = 10.0):
        self.backend = backend
        self.timeout = timeout
        self.recognizer = sr.Recognizer()

    def transcribe_file(self, audio_path: Union[str, Path]) -> VoiceTranscript:
        """Transcribes an audio file into a structured VoiceTranscript."""
        path = Path(audio_path)
        source_name = str(path)

        if self.backend not in SUPPORTED_BACKENDS:
            return VoiceTranscript(
                transcript="",
                source=source_name,
                confidence=None,
                timestamp=utc_now_iso(),
                audio_format="unknown",
                status="unavailable",
                backend=self.backend,
                error=f"STT backend '{self.backend}' is unavailable or not configured"
            )

        if not path.exists():
            return VoiceTranscript(
                transcript="",
                source=source_name,
                confidence=None,
                timestamp=utc_now_iso(),
                audio_format="unknown",
                status="error",
                backend=self.backend,
                error=f"Audio file not found: {source_name}"
            )

        try:
            file_size = path.stat().st_size
            if file_size == 0:
                return VoiceTranscript(
                    transcript="",
                    source=source_name,
                    confidence=None,
                    timestamp=utc_now_iso(),
                    audio_format="unknown",
                    status="error",
                    backend=self.backend,
                    error="Audio file is empty (0 bytes)"
                )

            fmt = path.suffix.lstrip(".").lower() or "wav"
            with open(path, "rb") as f:
                raw_bytes = f.read()

            return self.transcribe_bytes(raw_bytes, audio_format=fmt, source=source_name)

        except Exception as e:
            return VoiceTranscript(
                transcript="",
                source=source_name,
                confidence=None,
                timestamp=utc_now_iso(),
                audio_format="unknown",
                status="error",
                backend=self.backend,
                error=f"Failed to process audio file: {e}"
            )

    def transcribe_bytes(
        self,
        audio_bytes: bytes,
        audio_format: str = "wav",
        source: str = "bytes"
    ) -> VoiceTranscript:
        """Transcribes raw audio bytes into a structured VoiceTranscript."""
        if self.backend not in SUPPORTED_BACKENDS:
            return VoiceTranscript(
                transcript="",
                source=source,
                confidence=None,
                timestamp=utc_now_iso(),
                audio_format=audio_format,
                status="unavailable",
                backend=self.backend,
                error=f"STT backend '{self.backend}' is unavailable or not configured"
            )

        if not audio_bytes or len(audio_bytes) == 0:
            return VoiceTranscript(
                transcript="",
                source=source,
                confidence=None,
                timestamp=utc_now_iso(),
                audio_format=audio_format,
                status="error",
                backend=self.backend,
                error="Empty audio data provided"
            )

        try:
            in_fp = io.BytesIO(audio_bytes)
            # Decode audio using pydub
            audio_seg = AudioSegment.from_file(in_fp, format=audio_format)
            duration_s = round(len(audio_seg) / 1000.0, 2)

            # Convert to 16-bit PCM WAV for SpeechRecognition
            wav_fp = io.BytesIO()
            audio_seg.export(wav_fp, format="wav")
            wav_fp.seek(0)

            with sr.AudioFile(wav_fp) as audio_source:
                audio_data = self.recognizer.record(audio_source)

            # Query backend
            raw_res = self.recognizer.recognize_google(audio_data, show_all=True)

            if not raw_res or not isinstance(raw_res, dict) or not raw_res.get("alternative"):
                return VoiceTranscript(
                    transcript="",
                    source=source,
                    confidence=None,
                    timestamp=utc_now_iso(),
                    audio_format=audio_format,
                    status="unintelligible",
                    backend=self.backend,
                    duration_seconds=duration_s,
                    error="Speech was unintelligible or no speech detected"
                )

            best_alt = raw_res["alternative"][0]
            transcript_text = best_alt.get("transcript", "").strip()
            # Do NOT invent confidence values - use null (None) if not provided by backend
            confidence = best_alt.get("confidence", None)
            if confidence is not None:
                confidence = float(confidence)

            return VoiceTranscript(
                transcript=transcript_text,
                source=source,
                confidence=confidence,
                timestamp=utc_now_iso(),
                audio_format=audio_format,
                status="success",
                backend=self.backend,
                duration_seconds=duration_s,
                error=None
            )

        except sr.UnknownValueError:
            return VoiceTranscript(
                transcript="",
                source=source,
                confidence=None,
                timestamp=utc_now_iso(),
                audio_format=audio_format,
                status="unintelligible",
                backend=self.backend,
                error="Speech recognition could not understand audio"
            )
        except sr.RequestError as e:
            return VoiceTranscript(
                transcript="",
                source=source,
                confidence=None,
                timestamp=utc_now_iso(),
                audio_format=audio_format,
                status="error",
                backend=self.backend,
                error=f"Speech recognition service request error: {e}"
            )
        except Exception as e:
            return VoiceTranscript(
                transcript="",
                source=source,
                confidence=None,
                timestamp=utc_now_iso(),
                audio_format=audio_format,
                status="error",
                backend=self.backend,
                error=f"Audio processing failure: {e}"
            )

def transcribe_audio(
    source: Union[str, Path, bytes],
    audio_format: str = "wav",
    backend: str = "google_web_speech"
) -> VoiceTranscript:
    """Convenience helper to transcribe audio from file path or bytes."""
    transcriber = VoiceTranscriber(backend=backend)
    if isinstance(source, bytes):
        return transcriber.transcribe_bytes(source, audio_format=audio_format)
    return transcriber.transcribe_file(source)
