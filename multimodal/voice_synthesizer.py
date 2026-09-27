import io
import os
from pathlib import Path
import time
from typing import Optional, Union

from gtts import gTTS

from multimodal.schema import VoiceSynthesisResult, utc_now_iso
from services.config import LOGS_DIR

SUPPORTED_SYNTHESIS_BACKENDS = ["gtts"]

class VoiceSynthesizer:
    """Reusable Text-to-Speech synthesis utility."""

    def __init__(self, backend: str = "gtts", language: str = "en"):
        self.backend = backend
        self.language = language

    def synthesize(
        self,
        text: str,
        output_path: Optional[Union[str, Path]] = None
    ) -> VoiceSynthesisResult:
        """Synthesizes text into a real audio artifact on disk."""
        if not text or not str(text).strip():
            return VoiceSynthesisResult(
                text=str(text) if text is not None else "",
                output_audio_path=None,
                timestamp=utc_now_iso(),
                status="error",
                backend=self.backend,
                audio_bytes_length=0,
                error="Cannot synthesize empty or whitespace text"
            )

        if self.backend not in SUPPORTED_SYNTHESIS_BACKENDS:
            return VoiceSynthesisResult(
                text=text.strip(),
                output_audio_path=None,
                timestamp=utc_now_iso(),
                status="unavailable",
                backend=self.backend,
                audio_bytes_length=0,
                error=f"TTS backend '{self.backend}' is not supported or unavailable"
            )

        audio_bytes = None
        last_error = None

        tlds = ["com", "co.uk", "ca", "com"]
        for attempt in range(1, 5):
            tld = tlds[(attempt - 1) % len(tlds)]
            try:
                tts = gTTS(text=text.strip(), lang=self.language, tld=tld, timeout=10.0)
                fp = io.BytesIO()
                tts.write_to_fp(fp)
                audio_bytes = fp.getvalue()
                if audio_bytes and len(audio_bytes) > 0:
                    break
            except Exception as e:
                last_error = e
                time.sleep(0.6 * attempt)

        if not audio_bytes:
            return VoiceSynthesisResult(
                text=text.strip(),
                output_audio_path=None,
                timestamp=utc_now_iso(),
                status="error",
                backend=self.backend,
                audio_bytes_length=0,
                error=f"Text-to-speech synthesis failed after retries: {last_error}"
            )

        try:
            if output_path is not None:
                target_file = Path(output_path)
            else:
                audio_dir = LOGS_DIR / "audio"
                audio_dir.mkdir(parents=True, exist_ok=True)
                target_file = audio_dir / f"speech_{int(time.time() * 1000)}.mp3"

            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(audio_bytes)

            return VoiceSynthesisResult(
                text=text.strip(),
                output_audio_path=str(target_file.resolve()),
                timestamp=utc_now_iso(),
                status="success",
                backend=self.backend,
                audio_bytes_length=len(audio_bytes),
                error=None
            )

        except Exception as e:
            return VoiceSynthesisResult(
                text=text.strip(),
                output_audio_path=None,
                timestamp=utc_now_iso(),
                status="error",
                backend=self.backend,
                audio_bytes_length=0,
                error=f"Text-to-speech synthesis failed: {e}"
            )

def synthesize_speech(
    text: str,
    output_path: Optional[Union[str, Path]] = None,
    backend: str = "gtts"
) -> VoiceSynthesisResult:
    """Convenience helper to synthesize text into audio."""
    synthesizer = VoiceSynthesizer(backend=backend)
    return synthesizer.synthesize(text, output_path=output_path)
