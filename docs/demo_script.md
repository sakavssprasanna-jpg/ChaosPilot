# ChaosPilot Live Hackathon Demo Script (3–5 Minutes)
### "The Agentic Era: Supercharging RAG with Vision and Voice"

---

## ⏱️ Demo Timeline Overview

| Time | Phase | Focus | Key Message |
| :--- | :--- | :--- | :--- |
| **0:00 - 0:45** | **The Hook & Problem** | Architecture & Baseline | SREs face alert fatigue and fragmented signals. Naive LLMs hallucinate. |
| **0:45 - 1:30** | **The Outage** | Fault Injection & Propagation | Upstream Gateway 504 hides the root cause in downstream Payment. |
| **1:30 - 2:45** | **The Agent in Action** | Voice -> Vision -> RAG -> Evidence Graph | ChaosPilot correlates telemetry, runbooks, git, and visual diagrams. |
| **2:45 - 3:45** | **Safe Remediation & Verification** | Allowlisted Action & Verification | Zero arbitrary code. Recovery is proven empirically, never assumed. |
| **3:45 - 4:30** | **Voice Debrief & Evaluation** | Multimodal Output & Benchmark | 100% trace completeness, verbal debrief, reproducible benchmark. |

---

## 🎬 Step-by-Step Speaker Script & Actions

### Scene 1: Introduction & Baseline (0:00 - 0:45)

**Speaker Action:**
* Show the Streamlit Operator Console or terminal.
* Open the **Architecture Diagram** tab showing `Gateway :8000 -> Order :8001 -> Payment :8002`.

**Speaker Script:**
> *"Hello everyone! In production systems, when an outage strikes, SREs don't just need another chatbot spitting out boilerplate code. They need an agent that can see the architecture, hear operator instructions, ground its reasoning in real telemetry, runbooks, and git commits, and safely verify recovery.  
> This is **ChaosPilot** — an Agentic Multimodal SRE Agent.  
> Right now, we have three real FastAPI microservices running locally: API Gateway, Order Service, and Payment Service. Let's look at the baseline: traffic is flowing smoothly at 200 OK with sub-100ms latency."*

---

### Scene 2: Outage Simulation (0:45 - 1:30)

**Speaker Action:**
* In the Streamlit Console sidebar (or via CLI `python manage.py agent-incident --count 6`):
  Click **"Inject Timeout Fault"** (4.5s delay) and generate 6 checkout requests.
* Show the Gateway metrics spike to **100% error rate** with cascaded **504 Gateway Timeout** errors.

**Speaker Script:**
> *"Now, let's inject a realistic production failure. A recent configuration introduced an artificial 4.5-second processing delay into our Payment Service.  
> As live checkout traffic hits the Gateway, the requests exceed the upstream timeout budget. The Gateway immediately starts throwing `504 Gateway Timeout`.  
> An on-call engineer logging on sees only the Gateway failing. Is Order crashed? Is the database locked? Or is Payment timing out?"*

---

### Scene 3: Multimodal Perception & Grounded Investigation (1:30 - 2:45)

**Speaker Action:**
* Speak into the microphone (or enter text directive):  
  *"Investigate checkout timeout errors and identify degraded upstream dependencies."*
* Click **"Run Full Autonomous Incident Response"**.
* Point to the live **Event Timeline** streaming:
  1. *Detection*: Status degraded.
  2. *Investigation*: Correlating telemetry, runbooks via BM25, and isolated Git diffs.
  3. *Evidence Graph*: Constructing nodes and evaluating hypotheses.

**Speaker Script:**
> *"I'll issue a voice directive: 'Investigate checkout timeout errors.'  
> Notice what ChaosPilot does. It doesn't query an unbounded LLM with raw text.  
> First, it captures an atomic snapshot of real SQLite telemetry.  
> Second, it retrieves our internal operational runbooks using BM25, pulling the exact timeout budget specifications.  
> Third, it inspects recent Git commits to see recent changes.  
> Fourth, it constructs a directional **Evidence Graph**, linking telemetry signals, runbook sections, and diagnostic claims. It refutes the hypothesis that Order Service crashed because telemetry confirms Order Service has zero internal errors. Instead, it confirms the Payment Timeout propagation hypothesis with high confidence."*

---

### Scene 4: Safe Remediation & The Resolution Invariant (2:45 - 3:45)

**Speaker Action:**
* Point to Tab 4: **Actions & Verification**.
* Show the executed allowlisted action: `reset_controlled_fault` on `payment-service`.
* Show the 4-probe empirical verification passing (Health 200 OK, fault cleared, probe traffic 4/4 100% successful).

**Speaker Script:**
> *"Now comes the critical SRE difference: Safety.  
> ChaosPilot cannot execute arbitrary bash or shell scripts. Every action is gated by a Zero-Trust Policy Engine and executed via allowlisted tools.  
> The agent selects `reset_controlled_fault` to restore nominal latency.  
> But it does NOT assume it worked. It enters our **Empirical Verification Loop**: probing service health, checking fault state, and sending a real burst of probe traffic.  
> Only when all 4 probes pass does ChaosPilot mark the incident as `RESOLVED`. If verification fails, the UI refuses to declare victory."*

---

### Scene 5: Voice Debrief & Evaluation (3:45 - 4:30)

**Speaker Action:**
* Click **Play** on the synthesized audio debrief in the console.
* Switch to Tab 6: **Evaluation Benchmark**.
* Show the comparison matrix (Config A vs B vs C vs D).

**Speaker Script:**
> *"ChaosPilot synthesizes an audio debrief for the on-call engineer summarizing the root cause, actions taken, and verification results.  
> Finally, we evaluated ChaosPilot against traditional baselines. Config A (retrieval only) cannot diagnose or remediate. Config B (RAG + reasoning) diagnoses but cannot act. Only Config D (Multimodal Evidence Graph + Verification) achieves 100% trace completeness, zero hallucinated claims, and empirically verified recovery.  
> All of this runs locally on standard Python with zero external cloud dependencies.  
> That is ChaosPilot: bringing sight, sound, and empirical safety to the future of SRE. Thank you!"*

---

## 🛠️ Backup CLI Command Cheatsheet

If the browser console is unavailable, run the entire demo via the unified CLI:

```powershell
# 1. Run full end-to-end incident demo
python manage.py agent-incident --count 6

# 2. Run progressive evaluation benchmark
python manage.py evaluate

# 3. Stop services after demo
python manage.py stop
```
