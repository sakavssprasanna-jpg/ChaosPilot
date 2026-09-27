from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx
import streamlit as st

from agent.models import (
    ActionStatus,
    ConfidenceLevel,
    HypothesisStatus,
    IncidentPhase,
    IncidentReport,
    IncidentState,
    VerificationStatus,
    utc_now_iso
)
from agent.orchestrator import IncidentOrchestrator
from agent.tools import ALLOWED_SERVICES
from multimodal.visualizer import generate_topology_diagram, generate_telemetry_chart
from multimodal.voice_synthesizer import VoiceSynthesizer
from multimodal.voice_transcriber import VoiceTranscriber
from sandbox.process_sandbox import ProcessTargetEnvironment
from services.config import GATEWAY_URL, PAYMENT_SERVICE_URL, LOGS_DIR
from telemetry.db import get_all_summaries, get_summary
from telemetry.snapshot import get_fault_status, create_incident_snapshot
from telemetry.traffic import generate_traffic

try:
    from streamlit_mic_recorder import mic_recorder
    MIC_AVAILABLE = True
except ImportError:
    MIC_AVAILABLE = False


# ---------------------------------------------------------------------------
# SRE Invariant & Formatter Helpers (Pure Logic, Testable)
# ---------------------------------------------------------------------------

def should_display_resolved(report: Optional[IncidentReport], state: Optional[IncidentState]) -> bool:
    """Verifies whether the UI is permitted to display 'RESOLVED'.

    A strict SRE invariant: The UI must NEVER display 'RESOLVED' unless:
    1. The report final_status is 'resolved'
    2. Verification results are present
    3. The latest verification result status is 'verified_recovered'
    """
    if not report or report.final_status != "resolved":
        return False
    if not state or not state.verification_results:
        return False
    latest_verif = state.verification_results[-1]
    return getattr(latest_verif, "status", None) == VerificationStatus.VERIFIED_RECOVERED.value


def format_debrief_text(report: IncidentReport, state: IncidentState) -> str:
    """Formats an explainable voice debrief script from grounded incident facts."""
    action_summary = "recovery action was executed"
    if state and state.executed_actions:
        act = state.executed_actions[0]
        action_summary = f"{act.tool_name} was executed on {act.target}"

    verif_summary = "verification probes confirmed complete recovery"
    if state and state.verification_results:
        verif_summary = state.verification_results[-1].summary

    return (
        f"ChaosPilot incident response report for incident {report.incident_id[:12]}. "
        f"Status: {report.final_status}. "
        f"Root cause: {report.root_cause}. "
        f"Remediation: {action_summary}. "
        f"Verification: {verif_summary}"
    )


def get_multimodal_evidence_summary(state: IncidentState) -> Dict[str, int]:
    """Computes evidence item counts grouped by modality."""
    summary: Dict[str, int] = {
        "telemetry": 0,
        "text": 0,
        "git_diff": 0,
        "visual_observation": 0,
        "voice_transcript": 0
    }
    if not state or not state.evidence:
        return summary
    for item in state.evidence:
        modality = getattr(item, "modality", None) or (item.get("modality") if isinstance(item, dict) else None)
        if modality in summary:
            summary[modality] += 1
        elif modality:
            summary[str(modality)] = summary.get(str(modality), 0) + 1
    return summary


def extract_before_after_comparison(verification_result: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Extracts before_state and after_state from a VerificationResult."""
    if hasattr(verification_result, "before_state"):
        return getattr(verification_result, "before_state", {}), getattr(verification_result, "after_state", {})
    if isinstance(verification_result, dict):
        return verification_result.get("before_state", {}), verification_result.get("after_state", {})
    return {}, {}


# ---------------------------------------------------------------------------
# Streamlit Interactive Console Application
# ---------------------------------------------------------------------------

def main():
    # Page configuration
    st.set_page_config(
        page_title="ChaosPilot — Multimodal SRE Operations Console",
        page_icon="🛡️",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Initialize Session State
    if "env" not in st.session_state:
        st.session_state.env = ProcessTargetEnvironment()
    if "incident_state" not in st.session_state:
        st.session_state.incident_state = None
    if "incident_report" not in st.session_state:
        st.session_state.incident_report = None
    if "voice_transcript" not in st.session_state:
        st.session_state.voice_transcript = None
    if "debrief_audio_path" not in st.session_state:
        st.session_state.debrief_audio_path = None
    if "debrief_text" not in st.session_state:
        st.session_state.debrief_text = None
    if "operator_directive" not in st.session_state:
        st.session_state.operator_directive = "Investigate the payment outage and recover it safely."

    env: ProcessTargetEnvironment = st.session_state.env

    # ---------------------------------------------------------------------------
    # Sidebar: Target Environment & Incident Controls
    # ---------------------------------------------------------------------------

    st.sidebar.title("🛡️ ChaosPilot Sandbox")
    st.sidebar.caption("Deterministic Microservice Target & Chaos Controls")

    # Process Controls
    st.sidebar.subheader("Environment Controls")
    col_s1, col_s2 = st.sidebar.columns(2)
    with col_s1:
        if st.button("▶️ Start All", use_container_width=True):
            with st.spinner("Starting services..."):
                env.start_all()
                time.sleep(1.5)
                st.rerun()
    with col_s2:
        if st.button("⏹️ Stop All", use_container_width=True):
            with st.spinner("Stopping services..."):
                env.stop_all()
                time.sleep(0.5)
                st.rerun()

    # Fault Injection Controls
    st.sidebar.subheader("Controlled Fault Injection")
    fault_status = get_fault_status()
    is_fault_active = fault_status.get("active", False)

    if is_fault_active:
        st.sidebar.error(f"⚠️ FAULT ACTIVE: {fault_status.get('mode')} ({fault_status.get('delay_seconds')}s delay)")
    else:
        st.sidebar.success("✅ Nominal Operations (No Fault)")

    fault_mode = st.sidebar.selectbox("Fault Mode", ["timeout", "500", "503"], index=0)
    fault_delay = st.sidebar.slider("Artificial Processing Delay (seconds)", 1.0, 6.0, 4.5, 0.5)

    col_f1, col_f2 = st.sidebar.columns(2)
    with col_f1:
        if st.button("💥 Inject Fault", use_container_width=True):
            try:
                with httpx.Client(timeout=3.0) as client:
                    resp = client.post(f"{PAYMENT_SERVICE_URL}/fault/inject", json={
                        "mode": fault_mode,
                        "delay_seconds": fault_delay,
                        "error_code": 504,
                        "description": "Controlled payment-processing timeout fault (artificial delay exceeding upstream budget)"
                    })
                    if resp.status_code == 200:
                        st.sidebar.success("Fault injected!")
                        # Generate small burst to record 504 errors in SQLite WAL
                        generate_traffic(num_requests=4, concurrency=2, interval_seconds=0.03)
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.sidebar.error(f"HTTP {resp.status_code}: {resp.text}")
            except Exception as e:
                st.sidebar.error(f"Error injecting fault: {e}")

    with col_f2:
        if st.button("🔄 Reset Fault", use_container_width=True):
            try:
                with httpx.Client(timeout=3.0) as client:
                    resp = client.post(f"{PAYMENT_SERVICE_URL}/fault/reset")
                    if resp.status_code == 200:
                        st.sidebar.success("Fault reset!")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.sidebar.error(f"HTTP {resp.status_code}")
            except Exception as e:
                st.sidebar.error(f"Error resetting fault: {e}")

    st.sidebar.divider()

    # Test Checkout Traffic
    if st.sidebar.button("🛒 Send Test Checkout Request", use_container_width=True):
        t0 = time.time()
        try:
            with httpx.Client(timeout=6.0) as client:
                resp = client.post(f"{GATEWAY_URL}/api/checkout", json={
                    "customer_id": "cust_console_user",
                    "items": [{"item_id": "item_1", "name": "Item", "price": 25.0, "quantity": 1}],
                    "total_amount": 25.0
                })
                latency = round((time.time() - t0) * 1000, 1)
                if resp.status_code == 200:
                    st.sidebar.success(f"HTTP 200 OK ({latency}ms)")
                else:
                    st.sidebar.error(f"HTTP {resp.status_code} ({latency}ms): {resp.text[:100]}")
        except Exception as e:
            latency = round((time.time() - t0) * 1000, 1)
            st.sidebar.error(f"Request failed ({latency}ms): {e}")

    # ---------------------------------------------------------------------------
    # Header & Live System Status
    # ---------------------------------------------------------------------------

    st.title("🛡️ ChaosPilot — Multimodal SRE Operations Console")
    st.markdown("**Autonomous Incident Perception, Diagnosis, Safe Remediation & Verification**")
    st.caption("Stage 6 Multimodal Operator Console • Zero-Trust Sandboxed Architecture")

    # Query Live Health & Telemetry Metrics
    health_data = env.check_all_health()
    metrics_data = get_all_summaries(window_seconds=300.0)

    col_g, col_o, col_p = st.columns(3)

    def render_service_card(col, name, port, svc_key):
        h = health_data.get(svc_key, {})
        m = metrics_data.get(svc_key, {})
        status = h.get("status", "unknown")
        http_code = h.get("http_status", 0)
        details = h.get("details", {})

        with col:
            with st.container(border=True):
                if status == "healthy":
                    st.markdown(f"### 🟢 {name} `:{port}`")
                    st.caption(f"Status: **HEALTHY** (HTTP {http_code})")
                elif status == "degraded":
                    st.markdown(f"### 🟠 {name} `:{port}`")
                    st.caption(f"Status: **DEGRADED** (Fault: {details.get('fault_mode', 'timeout')})")
                else:
                    st.markdown(f"### 🔴 {name} `:{port}`")
                    st.caption(f"Status: **DOWN / UNREACHABLE**")

                c1, c2, c3 = st.columns(3)
                c1.metric("Requests", m.get("request_count", 0))
                c2.metric("Errors", m.get("error_count", 0))
                c3.metric("P95 Latency", f"{m.get('p95_latency_ms', 0.0)}ms")

    render_service_card(col_g, "API Gateway", 8000, "gateway")
    render_service_card(col_o, "Order Service", 8001, "order-service")
    render_service_card(col_p, "Payment Service", 8002, "payment-service")

    st.divider()

    # ---------------------------------------------------------------------------
    # Operator Directive Input (Text & Voice STT)
    # ---------------------------------------------------------------------------

    st.subheader("🎯 Operator Incident Directive")

    col_input, col_mic = st.columns([3, 1])

    with col_mic:
        st.markdown("**Voice Input (Mic)**")
        if MIC_AVAILABLE:
            audio_record = mic_recorder(
                start_prompt="🎙️ Record Voice",
                stop_prompt="⏹️ Stop Recording",
                key="operator_mic",
                just_once=False,
                use_container_width=True
            )
            if audio_record and "bytes" in audio_record and audio_record["bytes"]:
                raw_audio = audio_record["bytes"]
                with st.spinner("Transcribing real speech..."):
                    transcriber = VoiceTranscriber()
                    vt = transcriber.transcribe_bytes(raw_audio, audio_format="wav")
                    st.session_state.voice_transcript = vt
                    if vt.status == "success" and vt.transcript:
                        st.session_state.operator_directive = vt.transcript
                        st.success(f"Recognized: \"{vt.transcript}\"")
                    else:
                        st.warning(f"Voice STT: {vt.error or 'No speech recognized'}")
        else:
            st.info("Mic recorder not available in this environment.")

    with col_input:
        operator_text = st.text_input(
            "Operator Instruction / SRE Directive",
            value=st.session_state.operator_directive,
            key="directive_input",
            help="Specify the incident diagnosis and recovery directive for ChaosPilot."
        )
        if operator_text:
            st.session_state.operator_directive = operator_text

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        run_full = st.button("🚀 Run Full Incident Response (Diagnose & Recover)", type="primary", use_container_width=True)
    with col_btn2:
        run_investigate = st.button("🔍 Run Read-Only Investigation Only", use_container_width=True)

    # ---------------------------------------------------------------------------
    # Agent Execution Flow
    # ---------------------------------------------------------------------------

    if run_full or run_investigate:
        execute_recovery = bool(run_full)
        action_label = "Full Incident Response" if execute_recovery else "Read-Only Investigation"

        with st.status(f"ChaosPilot SRE Agent Running ({action_label})...", expanded=True) as status_box:
            st.write("Initializing target environment and event pipeline...")
            orchestrator = IncidentOrchestrator(env=env)

            # Execute orchestrator with real event callbacks
            t_start = time.time()
            if execute_recovery:
                state, report = orchestrator.run_incident_response(
                    execute_recovery=True,
                    directive=st.session_state.operator_directive
                )
                st.session_state.incident_state = state
                st.session_state.incident_report = report
            else:
                state = orchestrator.investigate(
                    directive=st.session_state.operator_directive
                )
                report = orchestrator.generate_report(state, create_incident_snapshot(env))
                st.session_state.incident_state = state
                st.session_state.incident_report = report

            duration = round(time.time() - t_start, 2)
            if should_display_resolved(report, state):
                status_box.update(label=f"ChaosPilot SRE Agent: INCIDENT RESOLVED ({duration}s)", state="complete", expanded=False)
            else:
                status_box.update(label=f"ChaosPilot SRE Agent Finished: {report.final_status.upper()} ({duration}s)", state="complete", expanded=False)

        st.rerun()

    # ---------------------------------------------------------------------------
    # Results: Timeline, Root Cause, Evidence, Recovery, Verification, Voice Debrief
    # ---------------------------------------------------------------------------

    if st.session_state.incident_report and st.session_state.incident_state:
        state: IncidentState = st.session_state.incident_state
        report: IncidentReport = st.session_state.incident_report

        st.divider()

        # 1. High-Level Incident Banner
        b_col1, b_col2, b_col3 = st.columns([2, 1, 1])
        with b_col1:
            st.markdown(f"### Incident Report: `{report.incident_id}`")
            if should_display_resolved(report, state):
                st.success("✅ **STATUS: VERIFIED RECOVERED & RESOLVED**")
            else:
                st.warning(f"⚠️ **STATUS: {report.final_status.upper()}**")
        with b_col2:
            st.metric("Total Evidence Items", len(state.evidence))
        with b_col3:
            st.metric("Actions Executed", len(state.executed_actions))

        # 2. Live Agent Timeline
        st.subheader("⏱️ Live Agent Event Timeline")
        with st.expander("View Chronological Agent Lifecycle Events", expanded=True):
            if state.events:
                for ev in state.events:
                    t_str = ev.get("timestamp", "").split("T")[-1][:8]
                    stage = ev.get("stage", "step").upper()
                    msg = ev.get("message", "")

                    icon = "⚙️"
                    if stage == "DETECTION":
                        icon = "🚨"
                    elif stage in ("INVESTIGATION", "TELEMETRY", "RAG", "GIT"):
                        icon = "🔎"
                    elif "HYPOTHESIS" in stage:
                        icon = "🧠"
                    elif "PLANNING" in stage:
                        icon = "📋"
                    elif "ACTION" in stage or stage == "REMEDIATION":
                        icon = "🛠️"
                    elif "VERIFICATION" in stage:
                        icon = "🔬"
                    elif stage == "RESOLVED":
                        icon = "✅"
                    elif stage == "FAILED":
                        icon = "❌"
                    elif stage == "DIRECTIVE":
                        icon = "🎯"

                    st.markdown(f"**`[{t_str}]` {icon} {stage}** — {msg}")
            else:
                for obs in state.observations:
                    st.markdown(f"- {obs}")

        # 3. Structured Deep SRE Diagnostic Tabs
        tab_root, tab_evidence, tab_recovery, tab_verify, tab_debrief, tab_graph = st.tabs([
            "🧠 Root Cause Analysis",
            "📂 Evidence (5 Modalities)",
            "🛠️ Actions & Recovery",
            "🔬 Before/After Verification",
            "🎙️ Spoken Debrief (TTS)",
            "🕸️ Evidence Graph & Evaluation"
        ])

        # Tab 1: Root Cause
        with tab_root:
            st.markdown("### Grounded Root Cause")
            st.info(f"**Root Cause:**\n\n{report.root_cause}")

            st.markdown("#### Root Cause Evidence Citations")
            if report.root_cause_evidence:
                for eid in report.root_cause_evidence:
                    st.markdown(f"- `[{eid}]`")
            else:
                st.caption("No specific citations assigned.")

            st.markdown("#### Candidate Hypotheses Considered")
            for h in state.hypotheses:
                st.markdown(f"**`[{h.status.upper()}]`** `{h.id}` (Confidence: **{h.confidence.upper()}**)")
                st.markdown(f"> *{h.statement}*")
                st.caption(f"Explanation: {h.explanation}")
                st.markdown("---")

        # Tab 2: Evidence
        with tab_evidence:
            st.markdown("### Multimodal Grounded Evidence")
            sub_tel, sub_rag, sub_git, sub_vis, sub_voice = st.tabs([
                "Telemetry", "Runbooks (RAG)", "Git History", "Visual Diagrams", "Voice"
            ])

            with sub_tel:
                tel_items = [e for e in state.evidence if getattr(e, "modality", None) == "telemetry"]
                for item in tel_items:
                    with st.expander(f"📊 {item.provenance} ({item.evidence_id})"):
                        st.json(item.content)

            with sub_rag:
                rag_items = [e for e in state.evidence if getattr(e, "modality", None) == "text"]
                for item in rag_items:
                    with st.expander(f"📖 {item.provenance} ({item.evidence_id})"):
                        st.markdown(item.content)

            with sub_git:
                git_items = [e for e in state.evidence if getattr(e, "modality", None) == "git_diff"]
                if git_items:
                    for item in git_items:
                        with st.expander(f"🌿 {item.provenance} ({item.evidence_id})"):
                            st.code(item.content, language="diff")
                else:
                    st.caption("No isolated Git commits were correlated for this diagnostic query.")

            with sub_vis:
                st.markdown("#### Locally Generated Visual Architecture & Performance Evidence")
                v_col1, v_col2 = st.columns(2)
                with v_col1:
                    st.markdown("**Service Topology Diagram**")
                    top_img_path = LOGS_DIR / "visualizations" / "topology_diagram.png"
                    if not top_img_path.exists():
                        generate_topology_diagram(top_img_path)
                    if top_img_path.exists():
                        st.image(str(top_img_path), use_container_width=True)
                with v_col2:
                    st.markdown("**Real Telemetry Chart (logs/telemetry.db)**")
                    tel_img_path = LOGS_DIR / "visualizations" / "telemetry_chart.png"
                    generate_telemetry_chart(tel_img_path)
                    if tel_img_path.exists():
                        st.image(str(tel_img_path), use_container_width=True)
                st.caption("Visual evidence derived directly from project configuration and SQLite WAL metrics.")

            with sub_voice:
                if st.session_state.voice_transcript:
                    vt: Any = st.session_state.voice_transcript
                    st.markdown(f"**Voice Input:** `{getattr(vt, 'source', 'mic')}`")
                    st.markdown(f"**Transcript:** \"{getattr(vt, 'transcript', '')}\"")
                    st.caption(f"Backend: {getattr(vt, 'backend', 'google_web_speech')} | Confidence: {getattr(vt, 'confidence', None) or 'null (uninvented)'} | Status: {getattr(vt, 'status', 'unknown')}")
                else:
                    st.caption("No voice command recorded for this session.")

        # Tab 3: Actions & Recovery
        with tab_recovery:
            st.markdown("### Recovery Planning & Execution")
            st.markdown("#### Planned Actions")
            if state.planned_actions:
                for i, pa in enumerate(state.planned_actions, 1):
                    st.markdown(f"**{i}. `{pa.tool_name}`** (Risk: `{pa.risk}`) — *{pa.purpose}*")
                    st.caption(f"Arguments: `{pa.arguments}`")
            else:
                st.caption("No recovery actions planned.")

            st.markdown("#### Executed Actions")
            if state.executed_actions:
                for ea in state.executed_actions:
                    if ea.status == ActionStatus.SUCCEEDED.value:
                        st.success(f"✅ **`{ea.tool_name}` on `{ea.target}`: SUCCEEDED**")
                    else:
                        st.error(f"❌ **`{ea.tool_name}` on `{ea.target}`: FAILED ({ea.error})**")
                    st.caption(f"Execution Window: {ea.started_at} -> {ea.finished_at}")
            else:
                st.caption("No recovery actions executed (investigation only).")

        # Tab 4: Verification
        with tab_verify:
            st.markdown("### Empirical Before / After Verification")
            if state.verification_results:
                vr = state.verification_results[-1]
                if vr.status == VerificationStatus.VERIFIED_RECOVERED.value:
                    st.success(f"✅ **VERIFICATION STATUS: {vr.status.upper()}**")
                else:
                    st.error(f"⚠️ **VERIFICATION STATUS: {vr.status.upper()}**")

                st.markdown(f"> *{vr.summary}*")

                # Before / After Comparison Table
                b_s, a_s = extract_before_after_comparison(vr)

                cmp_col1, cmp_col2 = st.columns(2)
                with cmp_col1:
                    with st.container(border=True):
                        st.markdown("#### 🔴 Before Recovery (Incident)")
                        st.markdown(f"- **Overall Status:** `{b_s.get('overall_status')}`")
                        st.markdown(f"- **Payment Health:** `{b_s.get('payment_health')}`")
                        st.markdown(f"- **Fault Active:** `{b_s.get('fault_active')}`")
                        st.markdown(f"- **Gateway Error Rate:** `{b_s.get('gateway_error_rate', 0)*100:.1f}%`")
                        st.markdown(f"- **Gateway Timeouts:** `{b_s.get('gateway_timeouts')}`")

                with cmp_col2:
                    with st.container(border=True):
                        st.markdown("#### 🟢 After Recovery (Verified)")
                        st.markdown(f"- **Overall Status:** `{a_s.get('overall_status')}`")
                        st.markdown(f"- **Payment Health:** `{a_s.get('payment_health')}`")
                        st.markdown(f"- **Fault Active:** `{a_s.get('fault_active')}`")
                        st.markdown(f"- **Probe Success:** `{a_s.get('probe_successful_requests')}` successful")
                        st.markdown(f"- **Probe Avg Latency:** `{a_s.get('probe_avg_latency_ms')}ms`")

                st.markdown("#### Verification Check Results")
                for c in vr.checks:
                    if c.get("passed"):
                        st.markdown(f"- ✅ **{c.get('check')}**: Passed")
                    else:
                        st.markdown(f"- ❌ **{c.get('check')}**: Failed")
            else:
                st.caption("No recovery verification executed.")

        # Tab 5: Voice Debrief
        with tab_debrief:
            st.markdown("### Multimodal Voice Postmortem Debrief")
            st.caption("Synthesizes a realistic spoken SRE audio summary from the actual incident report.")

            if st.button("🎙️ Generate Spoken Debrief", use_container_width=True):
                debrief_text = format_debrief_text(report, state)
                st.session_state.debrief_text = debbrief_text if (debbrief_text := debrief_text) else ""

                with st.spinner("Synthesizing audio debrief via gTTS..."):
                    synth = VoiceSynthesizer()
                    synth_res = synth.synthesize(debrief_text)
                    if synth_res.status == "success" and synth_res.output_audio_path:
                        st.session_state.debrief_audio_path = synth_res.output_audio_path
                        st.success("Spoken debrief synthesized successfully!")
                    else:
                        st.error(f"TTS synthesis failure: {synth_res.error}")

            if st.session_state.debrief_text:
                st.markdown(f"**Debrief Script:**\n\n> *\"{st.session_state.debrief_text}\"*")

            if st.session_state.debrief_audio_path and Path(st.session_state.debrief_audio_path).exists():
                st.audio(st.session_state.debrief_audio_path, format="audio/mp3")
                st.caption(f"Audio Artifact: `{st.session_state.debrief_audio_path}`")

        # Tab 6: Evidence Graph & Progressive Evaluation
        with tab_graph:
            st.markdown("### 🕸️ Empirical Evidence Graph & Progressive Evaluation")
            st.caption("Explicit causal and correlational graph connecting telemetry, runbooks, topology, claims, hypotheses, actions, and verification.")

            from agent.evidence_graph import EvidenceGraph, EvidenceGraphBuilder
            from evaluation import SystemEvaluator

            graph = None
            if getattr(state, "evidence_graph", None):
                graph = EvidenceGraph.from_dict(state.evidence_graph)
            else:
                graph = EvidenceGraphBuilder().build_graph(state)

            # Top-level Graph Metrics
            g1, g2, g3 = st.columns(3)
            g1.metric("Graph Nodes", len(graph.nodes))
            g2.metric("Evidence Edges", len(graph.edges))
            g3.metric("Grounded Claims", len(graph.claims))

            sub_view, sub_claims, sub_eval = st.tabs([
                "📊 Topology & Evidence Graph",
                "⚖️ Grounded Claims Analysis",
                "📈 Benchmark & Baseline Evaluation"
            ])

            with sub_view:
                st.markdown("#### Topological & Causal Evidence Graph (Mermaid)")
                mermaid_code = graph.to_mermaid(direction="TD")
                st.markdown(f"```mermaid\n{mermaid_code}\n```")

                with st.expander("🔍 Inspect Raw Graph JSON (Serializable)", expanded=False):
                    st.json(graph.to_dict())

            with sub_claims:
                st.markdown("#### Grounded Claims Evaluation")
                st.caption("Qualitative support strength derived strictly from empirical evidence. No fabricated probabilities.")

                for claim in graph.claims.values():
                    badge = "⚪ UNSUPPORTED"
                    if claim.support_strength == "strongly_supported":
                        badge = "🟢 STRONGLY SUPPORTED"
                    elif claim.support_strength == "supported":
                        badge = "🟢 SUPPORTED"
                    elif claim.support_strength == "weakly_supported":
                        badge = "🟡 WEAKLY SUPPORTED"
                    elif claim.support_strength == "contradicted":
                        badge = "🔴 CONTRADICTED"

                    with st.container(border=True):
                        st.markdown(f"**`[{badge}]`** `{claim.claim_id}`")
                        st.markdown(f"> *{claim.statement}*")

                        c_col1, c_col2 = st.columns(2)
                        with c_col1:
                            st.markdown("**Supporting Evidence:**")
                            if claim.evidence_ids:
                                for eid in claim.evidence_ids:
                                    st.markdown(f"- `[{eid}]`")
                            else:
                                st.caption("None (0 empirical support)")

                        with c_col2:
                            st.markdown("**Contradicting Evidence:**")
                            if claim.contradictions:
                                for cid in claim.contradictions:
                                    st.markdown(f"- `[{cid}]`")
                            else:
                                st.caption("None (0 contradictions)")

            with sub_eval:
                st.markdown("#### Progressive Baseline Evaluation")
                st.caption("Measurable comparison across 4 configurations on the controlled incident benchmark fixture.")

                evaluator = SystemEvaluator()
                eval_res = evaluator.evaluate_all(sample_snapshot=report.initial_state if hasattr(report, "initial_state") else None)
                st.markdown(eval_res.summary_markdown)


if __name__ == "__main__":
    main()
