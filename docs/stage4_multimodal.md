# ChaosPilot — Stage 4: Multimodal Perception Layer

## 1. Overview

Stage 4 equips ChaosPilot with multimodal perception capabilities (Voice + Vision + Telemetry + Text RAG + Git analysis) without introducing autonomous reasoning, remediation, or speculative AI execution. 

All perceptions are parsed into structured evidence items adhering to strict provenance rules. No confidence values or transcripts are fabricated, and vision models never report hallucinated observations when credentials are unconfigured.

---

## 2. Voice Input Flow (Speech-to-Text)

- **Module:** `multimodal/voice_transcriber.py` (`VoiceTranscriber`, `transcribe_audio`)
- **Backend:** `google_web_speech` (via `SpeechRecognition 3.17.0`)
- **Audio Processing:** Uses `pydub 0.25.1` and `static_ffmpeg 3.0` to convert any standard audio stream (`.wav`, `.mp3`, `.ogg`, `.webm`, `.flac`) into in-memory 16-bit PCM WAV.
- **Workflow:**
  ```
  Raw Audio (File / Bytes)
          ↓
  Pydub AudioSegment (Static FFmpeg)
          ↓
  16-bit PCM WAV Buffer
          ↓
  SpeechRecognition Recognizer
          ↓
  VoiceTranscript Object
  ```
- **Output Schema (`VoiceTranscript`):**
  ```json
  {
    "transcript": "payment service timeout error",
    "source": "logs/audio/input.wav",
    "confidence": null,
    "timestamp": "2026-09-27T18:30:00Z",
    "audio_format": "wav",
    "status": "success",
    "backend": "google_web_speech",
    "duration_seconds": 2.45,
    "error": null
  }
  ```
- **Fidelity Guarantees:**
  - If the engine provides no confidence score, `confidence` is strictly `null` (never a fabricated number).
  - On silence, empty bytes, corrupt files, or recognition failure, `status` is set to `"unintelligible"` or `"error"`, with `transcript: ""` and `confidence: null`.
  - Unconfigured backends return `status: "unavailable"` with descriptive error diagnostics.

---

## 3. Voice Output Flow (Text-to-Speech)

- **Module:** `multimodal/voice_synthesizer.py` (`VoiceSynthesizer`, `synthesize_speech`)
- **Backend:** `gTTS 2.5.4`
- **Output:** Genuine MP3 audio artifacts written to `logs/audio/speech_<timestamp>.mp3` or specified file path.
- **Workflow:**
  ```
  Plain Text
          ↓
  Validation (rejects empty/whitespace text)
          ↓
  gTTS Engine (with 3x exponential backoff retry)
          ↓
  MP3 Audio Disk Artifact
          ↓
  VoiceSynthesisResult Object
  ```
- **Output Schema (`VoiceSynthesisResult`):**
  ```json
  {
    "text": "Payment Service has timed out after 5.0 seconds.",
    "output_audio_path": "C:/Users/.../logs/audio/speech_1790514845.mp3",
    "timestamp": "2026-09-27T18:30:00Z",
    "status": "success",
    "backend": "gtts",
    "audio_bytes_length": 25536,
    "error": null
  }
  ```
- **Fidelity Guarantees:**
  - Empty or whitespace-only text is rejected with `status: "error"`.
  - Transient network issues trigger automated retries before reporting `status: "error"`.
  - Unsupported backends return `status: "unavailable"`.

---

## 4. Vision Input & Discovery Flow

- **Module:** `multimodal/vision_inspector.py` (`VisionInspector`, `discover_vision_backend`)
- **Discovery Mechanism:** Checks for `GEMINI_API_KEY` or `GOOGLE_API_KEY` in environment variables.
  - When keys are present: `backend = "gemini"`, `available = True`.
  - When keys are absent: `backend = "none"`, `available = False`.
- **Validation:** Pillow (`PIL.Image`) verifies file existence, non-zero byte size, and header/payload format integrity.
- **Visual Inspection Behaviors:**
  1. **Default (`backend="none"` / No API Key):**
     - Returns `status: "unavailable"`.
     - `observations: []`.
     - Error: `"Vision backend unavailable: GEMINI_API_KEY or GOOGLE_API_KEY not configured. No visual model ran."`
     - Strictly prevents hallucinating AI vision analysis.
  2. **Local Structural Analysis (`backend="local_structural"`):**
     - Employs Pillow to extract verified structural properties: format, resolution, aspect ratio, color mode.
     - Reports genuine image measurements without pretending an LLM ran.
  3. **Gemini Vision (`backend="gemini"`):**
     - Interfaces with `google.genai` model `gemini-2.5-flash` when credentials exist.

---

## 5. Visual Evidence Generators

- **Module:** `multimodal/visualizer.py`
- **Zero Fabrication Guarantee:** Visualizations are strictly derived from real configuration and SQLite WAL data.
- **Topology Diagram (`generate_topology_diagram`):**
  - Renders the exact three-tier microservice architecture (`Gateway:8000` $\rightarrow$ `Order:8001` $\rightarrow$ `Payment:8002` + in-memory store) based on `services/config.py` and `knowledge/architecture/service_topology.md`.
  - Saved to `logs/visualizations/topology_diagram.png`.
- **Telemetry Performance Chart (`generate_telemetry_chart`):**
  - Connects to SQLite database `logs/telemetry.db` and queries real `request_metrics` rows.
  - Plots latency over time (with error markers for status $\ge 500$) and HTTP status distributions (200, 500, 504).
  - Saved to `logs/visualizations/telemetry_chart.png`.

---

## 6. Unified Multimodal Evidence Schema

- **Module:** `multimodal/schema.py` (`MultimodalEvidenceItem`, `MultimodalEvidenceBundle`)
- **Coordinated Modalities:**
  1. `text`: Knowledge chunks (Runbooks, Architecture specs, Postmortems)
  2. `git_diff`: Historical commit messages and diff patches
  3. `telemetry`: Real health states and SQLite request metrics
  4. `voice_transcript`: Transcribed speech evidence from operator audio
  5. `visual_observation`: Verified structural/model observations from image artifacts
- **Item Schema:**
  ```json
  {
    "evidence_id": "voice_1790514845",
    "modality": "voice_transcript",
    "source": "logs/audio/speech.mp3",
    "timestamp": "2026-09-27T18:30:00Z",
    "content": "gateway service timeout error",
    "provenance": "audio:logs/audio/speech.mp3 (backend=google_web_speech)",
    "status": "verified",
    "metadata": {
      "confidence": null,
      "audio_format": "mp3",
      "duration_seconds": 2.45
    }
  }
  ```

---

## 7. CLI Reference

```bash
# Transcribe audio file
python manage.py transcribe logs/audio/sample.mp3

# Synthesize speech from text
python manage.py synthesize "Payment service is failing health checks"

# Inspect image with vision inspector
python manage.py inspect-image logs/visualizations/topology_diagram.png --backend local_structural

# Generate service topology diagram
python manage.py visualize-topology

# Generate real telemetry performance chart
python manage.py visualize-telemetry

# Assemble unified multimodal evidence bundle
python manage.py multimodal-evidence --image logs/visualizations/topology_diagram.png
```

---

## 8. Limitations & Boundary Guarantees

1. **No Autonomous Actions:** Stage 4 performs perception only; it cannot autonomously restart services, mutate configs, or trigger remediation.
2. **Network Dependency for Web Speech / gTTS:** The standard voice utilities utilize Google Web Speech and Google Translate TTS APIs. If offline, synthesis/transcription return clean structured `status: "error"` / `status: "unavailable"` objects without breaking.
3. **No Fabricated Observations:** When Gemini credentials are not present, vision analysis cleanly reports `status: "unavailable"` rather than hallucinating incident root causes.
