# ChaosPilot: Technical Resume & Project Descriptions
### Rigorous, Fact-Checked Project Bullet Points for Portfolios & Resumes

---

## 📌 One-Line Summary
**ChaosPilot:** An autonomous Multimodal SRE Agent that diagnoses cascading microservice outages and executes safe, empirically verified recovery using voice, structural vision, BM25 RAG, and directional evidence graphs.

---

## 💼 Resume Bullet Points (Tailored by Role)

### Option 1: AI / Agentic Systems Engineer Focus
* **Architected ChaosPilot**, an autonomous multimodal SRE agent integrating Voice STT/TTS (SpeechRecognition, gTTS), visual topology parsing (Pillow), and BM25 runbook retrieval into a closed-loop incident response pipeline.
* **Engineered a Directional Evidence Graph** tracking multi-tier provenance across telemetry, Markdown runbooks, and isolated Git diffs, eliminating LLM hallucinations via 5 qualitative claim grounding categories.
* **Implemented an Empirical Verification Engine** that enforces closed-loop recovery validation across HTTP health checks, fault resets, and live synthetic traffic bursts before declaring incident resolution.
* **Designed a Rigorous Evaluation Framework** benchmarking 4 progressive system architectures (Retrieval-Only vs. Multimodal Evidence Graph), achieving 100% evidence trace completeness on controlled failure scenarios.

### Option 2: SRE / Systems / Distributed Systems Focus
* **Built a Real 3-Tier Microservice Sandbox** (Gateway `:8000` -> Order Service `:8001` -> Payment Service `:8002`) using FastAPI and SQLite WAL telemetry to simulate cascading timeout budget propagation failures (`504 Gateway Timeout`).
* **Designed a Zero-Trust Safety Execution Layer** eliminating arbitrary shell commands through pre-registered tool allowlists, parameter sanitization, and a phase-gated Policy Engine.
* **Implemented an Automated Process Sandbox** in Python managing OS subprocess lifecycles, PID tracking, and socket recycling, enabling reproducible chaos injection on Windows without Docker dependencies.
* **Authored a 117-Test Regression Suite** (Pytest, AsyncIO) verifying cross-service fault injection, SQLite telemetry aggregation, BM25 chunk retrieval, and multimodal perception integrity with 100% pass rate.

### Option 3: Full-Stack / Platform Engineering Focus
* **Developed a Full-Featured SRE Operator Console** in Streamlit featuring real-time incident event streaming, interactive topology diagrams, directional evidence graph visualizations, and audio debrief playback.
* **Integrated Asynchronous Telemetry Middleware** across distributed FastAPI microservices, capturing latency distributions, percentiles (p95), and status code histograms into high-throughput SQLite WAL storage.
* **Created a Unified CLI Management Interface** (`manage.py`) supporting end-to-end incident simulations, manual chaos injection, progressive benchmark evaluation, and artifact generation.

---

## 📊 Truthful, Verified Metrics Matrix

All metrics below are derived directly from empirical benchmark execution (`python manage.py evaluate` and `pytest`):

| Metric | Measured Value | Scope / Context |
| :--- | :--- | :--- |
| **Automated Test Coverage** | **117 / 117 Passed (100%)** | Unit, integration, safety, and evaluation tests across Stages 1–8 |
| **Retrieval Recall@3** | **100.0%** | BM25 section-preserving runbook retrieval on incident failure queries |
| **Retrieval Precision@3** | **66.67%** | Precision of top-3 runbook chunks containing exact diagnostic keywords |
| **Mean Reciprocal Rank (MRR)** | **1.0** | First relevant runbook chunk retrieved at rank 1 |
| **Evidence Trace Completeness** | **100.0%** | All root cause citations in final postmortem resolve to verified evidence |
| **False Resolution Count** | **0** | Resolution invariant strictly enforced (verified recovery required) |
| **Unsafe Action Violations** | **0** | Zero arbitrary shell execution; 100% allowlist policy adherence |
| **External Dependencies** | **0 Required** | 100% local operation without mandatory Docker or external API keys |

---

## 🛠️ Key Technical Keywords
`Agentic AI` · `Site Reliability Engineering (SRE)` · `Multimodal RAG` · `Evidence Graph` · `BM25 Information Retrieval` · `FastAPI` · `Python 3.12` · `SQLite WAL` · `Fault Injection` · `Zero-Trust Tool Execution` · `Speech-to-Text (STT)` · `Text-to-Speech (TTS)` · `Streamlit` · `Pytest` · `Distributed Tracing`
