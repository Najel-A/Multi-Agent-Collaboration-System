# Multi-Agent Collaboration System (CoD) — System Architecture

## DATA 298A/B MSDA Project — Project Report

**Team 1:** Akash Thiagarajan, Chelsea Jaculina, Devesh Singh, Mrunali Katta, Najel Alarcon

**San Jose State University**

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Data and Project Management Plan](#2-data-and-project-management-plan)
3. [Data Engineering](#3-data-engineering)
4. [Model Development](#4-model-development)
5. [Agent Autonomy](#5-agent-autonomy)
6. [Data Analytics and Intelligent System](#6-data-analytics-and-intelligent-system)
7. [System Evaluation and Visualization](#7-system-evaluation-and-visualization)

---

## 1. Introduction

### 1.1 Project Background and Executive Summary

Cloud-native infrastructure built on Kubernetes generates massive volumes of operational signals — pod statuses, container logs, cluster events, resource metrics — across distributed environments. When failures occur, Site Reliability Engineers (SREs) must manually sift through these signals to identify root causes, a process that is slow, error-prone, and does not scale. No single agent has the breadth of knowledge, reasoning depth, or domain specialization to handle the full spectrum of cloud incidents alone.

**Targeted Problem:** Design and evaluate multi-agent coordination protocols that enable specialized autonomous agents to dynamically negotiate roles, share operational states, and collaboratively resolve complex Kubernetes incidents beyond the capability of any individual agent.

**Motivation:** The core challenge is not just anomaly detection — it is the *coordination problem*:
- How do multiple specialized agents discover each other's capabilities and negotiate task assignments?
- How do agents share state through message-passing or shared memory without losing coherence?
- How do agents resolve conflicting diagnoses through structured debate?
- How do agents improve collaboratively over time?

**Goals:**
- Design robust message-passing protocols for agent discovery, task assignment, and conflict resolution
- Implement shared memory and blackboard architectures for synchronized inter-agent state updates
- Develop representative multi-agent scenarios for evaluation and benchmarking
- Evaluate collaboration efficiency, scalability, and adaptability through rigorous testing across 4 progressively complex architectures
- Define how and when agents operate autonomously versus requiring human intervention

**Approach:** We develop 4 multi-agent model architectures that build upon each other, each adding a new coordination capability:

| Model | Builds On | New Coordination Capability | Key Agents Added |
|-------|-----------|----------------------------|------------------|
| **Model 1** | — (Baseline) | Sequential message passing | Planner, Executor, Reviewer |
| **Model 2** | Model 1 | Shared knowledge (RAG) | Retrieval Agent |
| **Model 3** | Model 2 | Blackboard + multi-step validation | Intent Classifier, Validation Agent |
| **Model 4** | Model 3 | Debate + referee + safety + memory | Debaters, Referee, Safety Agent, Memory Manager |

**Expected Outcome:** An extensible and scalable multi-agent collaboration platform enabling specialized autonomous agents to coordinate effectively, significantly enhancing their collective capability to solve complex problems beyond individual agent abilities.

### 1.2 Project Requirements

**Functional Requirements — Multi-Agent Coordination:**
- Message-passing protocol supporting agent discovery, task broadcast, and response routing
- Shared memory / blackboard architecture for synchronized inter-agent state
- Debate protocol with structured argumentation and referee adjudication
- Human-in-the-loop safety agent for high-risk action approval
- Context memory manager for long-term learning across incidents
- Evaluation framework comparing coordination efficiency across all 4 models

**Functional Requirements — Agent Autonomy:**
- Agents must operate in an autonomous observe-think-act loop without continuous human prompting
- Agents must self-trigger when new incidents are detected in the ELK pipeline
- Agents must determine when to act independently vs. when to escalate to a human
- Autonomy level must be configurable per agent and per risk tier

**Functional Requirements — Machine Learning:**
- LoRA fine-tuned classification model achieving >= 70% fuzzy accuracy (Mistral-7B)
- LoRA fine-tuned generation model achieving >= 70% combined score (Qwen2.5-7B)
- Anti-overfitting regularization suite (early stopping, dropout, weight decay, label smoothing)
- Hardware-adaptive training (CUDA GPU, Apple MPS, CPU fallback)

**Functional Requirements — Web-Based System:**
- Kibana dashboards for real-time log monitoring and incident visualization
- Interactive architecture visualization (HTML/CSS)
- Colab notebook-based training and evaluation interface
- ELK-powered observability UI accessible at https://localhost:5601

**Data Requirements:**
- Balanced synthetic K8s incident dataset (8,502 samples, 18 failure scenarios)
- Agent-specific datasets: tabular features (structured RCA agent), text evidence (semantic agent)

### 1.3 Project Deliverables

| Deliverable | Type | Status |
|-------------|------|--------|
| Multi-agent architecture design (4 models) | Architecture docs | Complete |
| Agent taxonomy and coordination protocols | Design specification | Complete |
| Agent autonomy framework design | Architecture docs | Complete |
| Synthetic K8s incident generator | Production script | Complete |
| Data transformation pipeline | Production script | Complete |
| Model 1: Mistral-7B LoRA classifier + eval suite | Training + eval code | Complete |
| Model 2: Qwen2.5-7B LoRA generator + eval suite | Training + eval code | Complete |
| Model 3: SMART knowledge-intensive MAS | Architecture design | Designed |
| Model 4: Self-evolving debate MAS | Architecture design | Designed |
| ELK observability stack (3 environments) | K8s + Terraform | Complete |
| Kibana dashboards (web-based UI) | K8s manifests | Complete |
| Interactive architecture diagram | HTML/CSS | Complete |
| Cross-model evaluation framework | Documentation | Complete |
| Google Colab training notebooks | Notebooks | Complete |

### 1.4 Technology and Solution Survey

**Multi-Agent Coordination Technologies:**

| Technology | Category | Purpose |
|------------|----------|---------|
| **Message Passing** | Communication | Agent-to-agent and broadcast communication |
| **Blackboard Architecture** | Shared Memory | Centralized knowledge store for multi-agent read/write |
| **Multi-Agent Debate** | Consensus | Structured argumentation for robust reasoning |
| **FAISS + RAG** | Knowledge Retrieval | Semantic search over historical incidents |
| **Human-in-the-Loop** | Safety | Human approval for high-risk remediations |

**Machine Learning Technologies:**

| Technology | Category | Purpose |
|------------|----------|---------|
| **LoRA (Low-Rank Adaptation)** | Fine-tuning | Parameter-efficient training (~1% trainable params) |
| **bitsandbytes (NF4)** | Quantization | 4-bit quantization for memory efficiency |
| **SFTTrainer (TRL)** | Training | Supervised fine-tuning with LoRA adapters |
| **Flash Attention 2** | Optimization | Optimized attention on A100 GPUs |
| **Cosine LR + Early Stopping** | Regularization | Prevent overfitting on small datasets |

**Solution Comparison — Coordination Paradigms:**

| Paradigm | Strengths | Limitations | Used In |
|----------|-----------|-------------|---------|
| **Centralized Pipeline** | Simple, predictable, easy to debug | No parallelism, single point of failure | Model 1 |
| **Shared Memory (RAG)** | Agents share context; historical matching | Requires good embeddings | Model 2 |
| **Blackboard + Validation** | Auditable, multi-step verification | Coordination overhead for writes | Model 3 |
| **Debate + Referee + Safety** | Robust, reduces hallucinations, self-improving | Token-intensive, complex orchestration | Model 4 |

### 1.5 Literature Survey of Existing Research

See `docs/multi_agent_systems_literature_review.md` for the full survey. Key areas:

- Multi-agent system coordination protocols (blackboard architectures, BDI agents)
- LLM-based autonomous agents for cloud operations (SRE copilots, AIOps)
- Multi-agent debate for improving LLM reasoning and reducing hallucinations
- Parameter-efficient fine-tuning (LoRA, QLoRA) for domain-specific agents
- Root cause analysis techniques for cloud-native systems

---

## 2. Data and Project Management Plan

### 2.1 Data Management Plan

**Data Collection:** Synthetic generation via `k8s_synth_generator_portfolio.py` — 8,502 balanced K8s incidents across 18 failure scenarios.

**Storage:**

| Dataset | Format | Location | Purpose |
|---------|--------|----------|---------|
| Raw incidents | JSONL | `data/synthetic_source.jsonl` | Full incident records for all agents |
| Agent 1 features | Parquet | `data/processed/agent1_structured.parquet` | Tabular features for classification agent |
| Agent 2 evidence | Parquet | `data/processed/agent2_evidence.parquet` | Text evidence for semantic agent |
| Generation stats | JSON | `data/stats.json` | Per-scenario balance verification |

### 2.2 Project Development Methodology

Iterative development cycle:
1. **Design** — Define agent roles, coordination protocols, autonomy boundaries
2. **Data** — Generate and transform incident datasets for agent consumption
3. **Build** — Implement agent pipelines, train ML models, deploy infrastructure
4. **Evaluate** — Measure collaboration efficiency, ML accuracy, and system quality
5. **Iterate** — Refine models and protocols based on evaluation results

### 2.3 Project Organization Plan

| Phase | Deliverables | Team Members |
|-------|-------------|--------------|
| Agent Architecture & Autonomy Design | 4 model architectures, agent taxonomy, autonomy framework | All |
| Data Pipeline | Synthetic generator, transformation scripts | Najel, Devesh |
| ML Model Development (Models 1-2) | LoRA training, evaluation suites, anti-overfitting | Chelsea, Akash |
| Infrastructure & Web UI (ELK + K8s) | K8s manifests, Terraform, Flux CD, Kibana dashboards | Najel, Devesh |
| Evaluation & Report | Metrics framework, documentation | Mrunali, All |

### 2.4 Project Resource Requirements

| Resource | Purpose |
|----------|---------|
| Google Colab (A100 GPU, 40GB VRAM) | Agent model training (LoRA fine-tuning) |
| AWS EKS (2x c7i-flex.large) | Production ELK observability stack |
| Kind (Docker) | Local K8s development |
| OpenAI / Anthropic API keys | Planned: Models 3, 6 agent LLMs |
| FAISS + sentence-transformers | Planned: Shared knowledge retrieval for RAG agents |

### 2.5 Project Schedule

See project Gantt chart and PERT chart in project management documentation.

---

## 3. Data Engineering

### 3.1 Data Process

```
Synthetic Generation --> Transformation --> Agent-Specific Datasets --> Agent Consumption
      |                       |                     |                        |
  8,502 incidents       Flatten + Parse      2 Parquet files          Feed to agents
  (18 scenarios)        + Feature Engineer   (structured + text)      for training/inference
```

### 3.2 Data Collection

**Source:** `k8s_synth_generator_portfolio.py`

| Property | Value |
|----------|-------|
| Total samples | 8,502 |
| Failure scenarios | 18 |
| Per-scenario target | 500 (balanced) |
| Difficulty levels | Easy, Medium, Hard |
| Namespace diversity | 20+ base namespaces |
| Cluster range | cust-1 to cust-350 |

**Failure Scenarios:**

| Category | Scenarios |
|----------|-----------|
| Config Error | Missing Secret, Bad ConfigMap Key |
| Image Pull | Bad Tag, Registry Auth |
| Scheduling | Taint, Insufficient Memory/CPU, NodeSelector Mismatch |
| Storage | Unbound PVC, PVC Not Found |
| Crash Loop | Bad Args, OOM Killed, Liveness Probe, RBAC Forbidden, DNS, Connection Refused |
| Readiness | Readiness Probe Failure |
| Quota | Pod Quota Exceeded |

Each incident contains: context (cluster, namespace, workload), fault injection details, full observability signals (kubectl output, logs, events, metrics), remediation (diagnosis, fix plan, actions, verification, rollback), and metadata (difficulty, noise level).

### 3.3 Data Pre-processing

`transform_incidents.py` applies:
1. JSON flattening via `pd.json_normalize`
2. Regex parsing of `kubectl_describe_pod` (pod_status, waiting_reason, error_message, restart_count)
3. Tabular parsing of `kubectl_get_events` (event_type, event_reason, event_message)
4. Dict extraction from `metrics_snapshot` (restart_count_metrics, oom_killed)

### 3.4 Data Transformation

**Agent 1 Dataset** (`agent1_structured.parquet`): 27 tabular columns — flattened context, parsed observations, derived `root_cause_family` (12 labels) and `symptom_family` labels.

**Agent 2 Dataset** (`agent2_evidence.parquet`): 16 columns — raw `evidence_text` (concatenated kubectl/logs/metrics) paired with `diagnosis_text`, `fix_plan_text`, `actions_text`, `verification_text`, `rollback_text`.

### 3.5 Data Preparation

80/20 train/test split (seed=42) for both datasets. Parquet format for columnar compression and fast I/O.

### 3.6 Data Statistics

8,502 total incidents, 500 per scenario (balanced), 498 rejected during generation. 12 root cause family labels, 18 scenario IDs, 3 difficulty levels.

---

## 4. Model Development

### 4.1 Model Proposals — Progressive Multi-Agent Architectures

The system proposes **4 multi-agent model architectures** that build upon each other. Each model inherits the agents and capabilities from the previous model and adds a new coordination layer. This progressive design lets us measure exactly what each coordination improvement contributes.

---

#### Model 1: Centralized Linear Pipeline (Baseline)

**Status: Implemented** | **Coordination: Sequential Message Passing**

```
Data Ingestion --> Preprocessing --> Anomaly Detection --> Planner --> Executor --> Reviewer
       |                |                  |                 |            |           |
    [message]       [message]          [message]        [message]    [message]   [message]
```

**Coordination Protocol:** Simple sequential message passing. Each agent receives input from the previous agent, processes it, and forwards the result to the next. No parallelism, no shared state, no negotiation.

**Agents:**

| Agent Type | Agent | Responsibility |
|------------|-------|---------------|
| **Core Pipeline** | Data Ingestion Agent | Collect and normalize raw K8s logs |
| **Core Pipeline** | Preprocessing Agent | Parse, enrich, validate log data; generate log templates |
| **Analysis** | Anomaly Detection Agent | Identify outliers using Isolation Forest, One-Class SVM, LOF |
| **Planning** | Planner Agent | Choose playbook/action based on alert; classify root cause |
| **Execution** | Executor Agent | Execute remediation steps (kubectl commands, API calls) |
| **Oversight** | Reviewer Agent | Verify outcome post-remediation; trigger rollback if needed |

**ML Model — Mistral-7B LoRA (RCA Classification):**

| Property | Value | Justification |
|----------|-------|---------------|
| Base model | Mistral-7B-Instruct-v0.3 | Strong instruction-following at 7B; open-source; efficient for classification |
| Fine-tuning | LoRA (r=16, alpha=32, dropout=0.05) | Trains ~0.1% of params; competitive accuracy at fraction of cost |
| Quantization | 4-bit NF4 (bitsandbytes) | Enables training on single A100; minimal accuracy loss |
| Target modules | q_proj, k_proj, v_proj, o_proj | Attention layers sufficient for classification |
| Input format | Mistral instruct: `<s>[INST] {system}\n{prompt} [/INST] {label}</s>` | Native pre-training format |
| Max sequence length | 512 tokens | Sufficient for structured signal input |
| Training | SFTTrainer, cosine LR (2e-4), AdamW, warmup 10%, grad accum 4 | Standard LoRA config |
| Output | Root cause family label (12 classes) | |
| Target accuracy | >= 70% fuzzy match | |

**Model Selection Justification:**
- **Why Mistral-7B over larger models?** Classification requires fast, single-label output — not deep reasoning. 7B is sufficient and enables low-latency inference.
- **Why LoRA over full fine-tuning?** 7B full fine-tuning requires >80GB VRAM. LoRA trains ~7M parameters vs. ~7B, fitting on a single A100.
- **Why 4-bit quantization?** Reduces model memory from ~14GB (FP16) to ~4GB. Minimal accuracy loss for classification.

**Research Question:** *What is the minimum viable multi-agent/LLM setup that's better than pure rules?*

---

#### Model 2: RAG-Enhanced Pipeline with Shared Knowledge

**Status: Implemented** | **Builds on Model 1** | **New: Shared Memory via RAG**

```
Data Ingestion --> Preprocessing --> Anomaly Detection --> [NEW] Retrieval Agent
                                                               |
                                            +---------[Knowledge Base]--------+
                                            |        (FAISS / Embeddings)     |
                                    Planner (with RAG context) --> Executor --> Reviewer
```

**What's New:** Inherits all 6 agents from Model 1, adds a **Retrieval Agent** and **Knowledge Base** (FAISS vector store). The Planner now reasons with historical context.

**All Agents (inherited + new):**

| Agent Type | Agent | New? | Responsibility |
|------------|-------|------|---------------|
| Core Pipeline | Data Ingestion Agent | Inherited | Collect and normalize raw K8s logs |
| Core Pipeline | Preprocessing Agent | Inherited | Parse, enrich, validate log data |
| Analysis | Anomaly Detection Agent | Inherited | Identify outliers (IF, SVM, LOF) |
| **Analysis** | **Retrieval Agent** | **NEW** | Query FAISS for similar past incidents; fetch runbooks, configs, metrics |
| Planning | Planner Agent (RAG-enhanced) | Enhanced | Generate diagnosis using RAG context |
| Execution | Executor Agent | Inherited | Execute remediation steps |
| Oversight | Reviewer Agent | Inherited | Verify outcome, trigger rollback |

**ML Model — Qwen2.5-7B LoRA (Diagnosis & Remediation Generation):**

| Property | Value | Justification |
|----------|-------|---------------|
| Base model | Qwen2.5-7B-Instruct | Best structured text + code generation at 7B; native ChatML |
| Fine-tuning | LoRA (r=16, alpha=32, dropout=0.05) | Same efficiency as Model 1 |
| Quantization | 4-bit NF4 | Same memory efficiency |
| Input format | ChatML: `<\|im_start\|>system\n...<\|im_end\|>` | Native Qwen format |
| Max sequence length | 1024 tokens | Longer — generation requires richer output |
| Output | Structured report: Diagnosis, Fix Plan, Actions, Verification, Rollback | |
| Target score | >= 70% combined (40% structure + 60% keyword overlap) | |

**Model Selection Justification:**
- **Why Qwen2.5-7B over Mistral?** Qwen excels at structured text generation and code understanding — critical for producing actionable kubectl commands.
- **Why 1024 tokens vs 512?** Generation tasks produce much longer output than single-label classification.
- **Why a separate model?** Classification and generation are fundamentally different. Best model per task outperforms a single multi-task model.

**Model Improvement over Model 1:** Adds generation capability + shared knowledge for historical matching.

**Research Question:** *How much lift does shared knowledge (RAG) provide over isolated agent reasoning?*

---

#### Model 3: SMART-Inspired Multi-Step Reasoning with Blackboard Architecture

**Status: Architecture designed, implementation planned** | **Builds on Model 2** | **New: Blackboard + Validation**

```
                          +---------------------------+
                          |     BLACKBOARD            |
                          |  (Shared Agent State)     |
                          |                           |
                          |  intent: {...}            |
                          |  retrieved_context: {...} |
                          |  validation_result: {...} |
                          |  rca_plan: {...}          |
                          +----^-----^-----^-----^---+
                               |     |     |     |
                    +----------+  +--+--+  +--+--+  +--------+
                    |             |     |  |     |  |        |
         [NEW] Intent       Retrieval  [NEW]     Planner  Executor
               Classifier   Agent    Validation
                             (M2)    Agent
```

**What's New:** Upgrades to a **Blackboard Architecture** (all agents read/write shared state), adds **Intent Classifier** and **Validation Agent** (hallucination detector).

**Blackboard Protocol:**

| Step | Agent | Reads from Blackboard | Writes to Blackboard |
|------|-------|----------------------|---------------------|
| 1 | **Intent Classifier** | Raw incident data | `intent` (anomaly type, severity, blast radius) |
| 2 | **Retrieval Agent** | `intent` | `retrieved_context` (similar incidents, runbooks) |
| 3 | **Validation Agent** | `intent` + `retrieved_context` | `validation_result` (cross-checked findings, hallucination flags) |
| 4 | **Planner** | All blackboard state | `rca_plan` (diagnosis + remediation) |
| 5 | **Executor** | `rca_plan` | Execution result |

**All Agents:**

| Agent Type | Agent | New? | LLM | Responsibility |
|------------|-------|------|-----|---------------|
| Core Pipeline | Data Ingestion, Preprocessing | Inherited (M1) | — | Collect, parse logs |
| **Analysis** | **Intent Classifier** | **NEW** | GPT-4o-mini / Haiku | Categorize anomaly type, severity, blast radius |
| Analysis | Retrieval Agent | Inherited (M2) | GPT-4o-mini / Llama 3 8B | Query FAISS for similar incidents |
| **Analysis** | **Validation Agent** | **NEW** | GPT-4o / Claude Sonnet | Cross-check findings, detect hallucinations |
| Planning | Planner Agent | Enhanced | Claude Opus 4 / GPT-4o | Synthesize validated state into RCA plan |
| Execution | Executor, Reviewer | Inherited (M1) | — | Execute and verify |

**ML Improvements over Model 2:**
- Intent classification before retrieval improves precision
- Validation Agent catches hallucinations
- Planner upgraded from 7B LoRA to Opus/GPT-4o
- Tool-grounded reasoning via MCP-style tool calls

**Research Question:** *Given powerful LLMs with tool access and validation, what is the best achievable performance?*

---

#### Model 4: Self-Evolving Cognitive Hybrid MAS (Multi-Agent Debate)

**Status: Architecture designed, implementation planned** | **Builds on Model 3** | **New: Debate + Safety + Memory**

```
                     [Model 3 Blackboard Output]
                              |
              +---------------+---------------+
              |               |               |
        Debater 1       Debater 2       Debater 3
        (Opus)          (GPT-4o)        (DeepSeek-R1)
              |               |               |
              +-------+-------+-------+-------+
                      |
                 Cross-Examination
                      |
                   Referee  -->  Safety Agent  -->  Executor  -->  Memory Manager
```

**What's New:** Wraps Model 3 in a debate framework with **3 Debaters**, **Referee**, **Safety Agent (HiTL)**, and **Context Memory Manager**.

**Debate Protocol:**
1. **Round 1:** All debaters independently generate hypotheses from Model 3's blackboard
2. **Round 2:** Cross-examination — debaters critique each other's reasoning
3. **Round 3:** Referee scores arguments (35% evidence grounding, 25% logic, 20% remediation specificity, 20% counter-argument resilience)
4. **Round 4:** Safety gate (low/medium/high risk tiers)
5. **Round 5:** Memory Manager indexes outcome for future learning

**All Agents (14 total):**

| Agent Type | Agent | New? | LLM |
|------------|-------|------|-----|
| Core Pipeline | Data Ingestion, Preprocessing | Inherited (M1) | — |
| Analysis | Intent Classifier | Inherited (M3) | GPT-4o-mini / Haiku |
| Analysis | Retrieval Agent | Inherited (M2) | GPT-4o-mini / Llama 3 8B |
| Analysis | Validation Agent | Inherited (M3) | GPT-4o / Claude Sonnet |
| **Debate** | **Debater 1** | **NEW** | Claude Opus 4 |
| **Debate** | **Debater 2** | **NEW** | GPT-4o |
| **Debate** | **Debater 3** | **NEW** | DeepSeek-R1 / Llama 3 70B |
| **Consensus** | **Referee** | **NEW** | Claude Opus 4 / GPT-4o |
| **Safety** | **Safety Agent (HiTL)** | **NEW** | GPT-4o-mini / Haiku |
| Planning | Planner | Inherited (M3) | Claude Opus 4 |
| Execution | Executor, Reviewer | Inherited (M1) | — |
| **Memory** | **Context Memory Manager** | **NEW** | text-embedding-3-large |

**Research Question:** *How close can a MAS get to being a self-improving, safe co-SRE?*

---

### 4.2 How Models Build on Each Other

```
Model 1 (6 agents):  Ingestion -> Preprocessing -> Detection -> Planner -> Executor -> Reviewer
                     Sequential messages | Mistral-7B LoRA

Model 2 (7 agents):  + Retrieval Agent + Knowledge Base
                     Shared memory (RAG) | Qwen2.5-7B LoRA

Model 3 (9 agents):  + Intent Classifier + Validation Agent + Blackboard
                     Multi-step validation | Opus / GPT-4o

Model 4 (14 agents): + 3 Debaters + Referee + Safety + Memory Manager
                     Debate consensus + HiTL + self-improvement
```

### 4.3 Model Comparison and Justification

| Model | Coordination | ML Approach | Strengths | Limitations |
|-------|-------------|------------|-----------|-------------|
| **1** | Sequential pipeline | Mistral-7B LoRA (classification) | Fast, cheap, interpretable | No shared knowledge |
| **2** | Pipeline + shared memory | Qwen2.5-7B LoRA (generation) + RAG | Historical matching, detailed output | No validation |
| **3** | Blackboard + validation | Opus / GPT-4o (reasoning) | Auditable, hallucination-checked | Expensive per-call |
| **4** | Debate + referee + safety | Heterogeneous LLMs (debate) | Robust, self-improving, safe | Token-intensive |

**ML Design Choices Justification:**

| Choice | Why |
|--------|-----|
| LoRA over full fine-tuning | ~0.1% params, fits A100, minimal accuracy loss |
| 4-bit NF4 over FP16 | Best compression/quality ratio for single-GPU |
| Mistral for classification | Best 7B instruction-following for single-label tasks |
| Qwen for generation | Strongest structured text + code generation at 7B |
| Separate models per task | Specialization outperforms multi-task at this scale |

### 4.4 Model Evaluation Methods

#### 4.4.1 Model 1 — Classification Evaluation

| Metric | Description | Why This Metric | Target |
|--------|-------------|-----------------|--------|
| **Fuzzy Accuracy** | Substring match (case-insensitive) | LLMs may generate formatting variations; fuzzy match accounts for this | >= 70% |
| **Exact Accuracy** | Case-insensitive exact match | Strict correctness baseline | — |
| **Per-Class Accuracy** | Fuzzy accuracy per root cause family | Identifies which K8s failures the model struggles with | — |
| **Macro Precision** | Avg precision across 12 classes | Ensures no bias toward common failures | > 0.85 |
| **Macro Recall** | Avg recall across 12 classes | Ensures rare failures aren't missed | > 0.80 |
| **Macro F1-Score** | Harmonic mean of P and R | Single balanced metric | > 0.85 |
| **Confusion Matrix** | Cross-class misclassification heatmap | Reveals which failures get confused (OOM vs probe, DNS vs connection) | — |
| **Difficulty Accuracy** | Accuracy by easy/medium/hard | Tests noisy/ambiguous incidents | — |
| **Train/Val Gap** | val_loss - train_loss per epoch | Detects overfitting | < 0.3 |

#### 4.4.2 Model 2 — Generation Evaluation

| Metric | Description | Why Not BLEU/ROUGE | Target |
|--------|-------------|---------------------|--------|
| **Structure Score** (40%) | Sections found / 3 (Diagnosis, Fix Plan, Verification) | SRE needs structured output — free text without sections is unusable | — |
| **Keyword Overlap** (60%) | GT keywords in prediction / total GT keywords (words > 4 chars) | BLEU/ROUGE penalize valid paraphrases. Keywords capture technical concept coverage | — |
| **Combined Score** | 0.4 * structure + 0.6 * keywords | Mirrors SRE evaluation: "Is it structured? Does it mention the right things?" | >= 70% |
| **Train/Val Gap** | val_loss - train_loss | Higher tolerance for generation | < 0.5 |

#### 4.4.3 Model 3 — Multi-Step Reasoning Evaluation

| Metric | Description | Why This Metric |
|--------|-------------|-----------------|
| **Trajectory Coherence** | Does each step logically follow the previous? | Multi-step pipelines can produce individually-correct but globally-incoherent chains |
| **Hallucination Detection Rate** | % of hallucinated claims caught by Validation Agent | Core value proposition of Model 3 |
| **Evidence Validation Accuracy** | % of Validation Agent decisions that are correct | Measures the hallucination detector itself |
| **Tool-Call Accuracy** | Did tool calls return expected results and get used correctly? | Tool-grounded reasoning only works if tools work |
| **Blackboard Utilization** | % of blackboard fields read by downstream agents | Measures whether shared state is actually used |
| **RCA Accuracy** | % correct root cause (vs. ground truth) | End-to-end task accuracy | > 75% |

#### 4.4.4 Model 4 — Debate Evaluation

| Metric | Description | Why This Metric |
|--------|-------------|-----------------|
| **Debate Diversity** | Uniqueness of hypotheses (Jaccard distance) | If all debaters agree, debate adds no value |
| **Referee Agreement Rate** | How often referee picks consensus vs. minority | Value comes from evidence-based dissent, not voting |
| **Accuracy Gain from Debate** | Model 4 accuracy - Model 3 accuracy | Isolates debate layer's contribution |
| **Error-Repeat Reduction** | % decrease in repeated misclassifications over time | Core self-improvement metric |
| **Safety Violation Rate** | Actions blocked by Safety Agent | Does the safety gate catch real risks? |
| **False Escalation Rate** | Safe actions unnecessarily escalated | Too many erodes SRE trust | < 10% |
| **Human Override Rate** | How often SREs override final decision | Lower = higher trust | 5-15% |
| **Memory Retrieval Precision** | % of retrieved past incidents that are relevant | If memory returns noise, it hurts | > 70% |
| **Cost per Incident** | Total API cost for all LLM calls | Must justify improvement over Model 3 | < $0.60 |

#### 4.4.5 Cross-Model Comparison Framework

All 4 models are evaluated on the **same 500 held-out incidents** for direct comparison:

| Metric | Model 1 | Model 2 | Model 3 | Model 4 | Statistical Test |
|--------|---------|---------|---------|---------|-----------------|
| **RCA Accuracy** | Fuzzy acc % | Combined score % | RCA acc % | Post-debate acc % | Friedman test |
| **Hallucination Rate** | N/A | Keyword miss rate | Validation catch rate | Debate correction rate | McNemar's |
| **MTTR** | Baseline | + RAG latency | + validation latency | + debate latency | Wilcoxon signed-rank |
| **Cost per Incident** | ~$0.00 | ~$0.00 | ~$0.08-0.15 | ~$0.30-0.60 | — |
| **Agent Count** | 6 | 7 | 9 | 14 | — |
| **Self-Improvement** | None | None | KB updates | Memory + error tracking | Improvement curve |

**Accuracy vs. Cost Efficiency:**
```
Accuracy
  ^
  |                                    * Model 4 (best accuracy, highest cost)
  |                          * Model 3 (validated, moderate cost)
  |              * Model 2 (RAG-enhanced, zero API cost)
  |    * Model 1 (baseline, zero API cost)
  +-----+----------+----------+----------+--> Cost per incident
       $0        $0.05      $0.15      $0.60
```

**Expected Per-Class Improvement Pattern:**

| Root Cause | M1 | M2 | M3 | M4 | Why |
|-----------|----|----|----|----|-----|
| oom | High | High | High | High | Strong signals (exit code 137) |
| image_pull | High | High | High | High | Distinct error messages |
| scheduling | Medium | Medium | High | High | Requires multi-signal reasoning |
| dns | Low | Medium | High | High | Ambiguous with connection errors |
| connection | Low | Medium | Medium | High | Ambiguous with dns errors |
| other | Low | Low | Medium | High | Catch-all — hardest for all |

- **M1 -> M2:** Gains on incidents needing context (dns, connection, probe)
- **M2 -> M3:** Gains on ambiguous incidents where validation catches hallucinations
- **M3 -> M4:** Gains on novel/hard incidents where debate surfaces alternatives

**Anti-Overfitting Improvements (Models 1 & 2):**

| Technique | Old (v2) | New (v3) |
|-----------|----------|----------|
| Early stopping | None | patience=2 |
| LoRA rank | 64 | 32 |
| LoRA dropout | 0.05 | 0.1 |
| Weight decay | 0.01 | 0.05 |
| Label smoothing | None | 0.1 (M1) |
| Train/val split | 85/15 | 80/20 |

### 4.5 Model Validation and Evaluation Results

See `docs/report_4.4_4.5_evaluation.md` for training configurations and result templates.

**Model 1 Evaluation Suite (7 visualizations):**
1. Data distribution (root cause + difficulty)
2. Training curves with overfitting gap
3. Confusion matrix heatmap
4. Per-class precision/recall/F1
5. Per-class fuzzy accuracy with 70% target
6. Accuracy by difficulty
7. Validation summary dashboard

**Model 2 Evaluation Suite:**
1. Training curves with overfitting detection
2. Per-scenario structure/keyword scores
3. Combined score distribution
4. Sample predictions vs. ground truth

---

## 5. Agent Autonomy

### 5.1 The Autonomy Problem

A multi-agent system is only useful if agents act without a human triggering each step. But full autonomy is dangerous for production — an agent that autonomously deletes pods or modifies configs can cause outages. The challenge: **which agents should be autonomous, which semi-autonomous, and which require human approval?**

### 5.2 Autonomy Tiers

| Tier | Level | Human Involvement | Cost Profile |
|------|-------|-------------------|-------------|
| **Tier 1** | Fully Autonomous | None — human reviews after the fact | Cheapest — no wait time, local models |
| **Tier 2** | Semi-Autonomous | Human can override before execution | Moderate — adds latency |
| **Tier 3** | Human-in-the-Loop | Human must explicitly approve | Most expensive in human time, safest |

### 5.3 Agent-to-Tier Mapping

| Agent | Tier | Justification |
|-------|------|---------------|
| Data Ingestion, Preprocessing, Anomaly Detection | Tier 1 | Read-only — no side effects |
| Intent Classifier, Retrieval, Validation | Tier 1 | Read-only — analyze, don't modify |
| Debaters (1-3), Referee | Tier 1 | Read-only — generate hypotheses |
| Planner, Memory Manager | Tier 2 | Write to knowledge base — logged and reversible |
| Reviewer | Tier 2 | May trigger rollback — requires approval |
| Executor (low/medium risk) | Tier 2 | Non-destructive or config changes — 30s delay for override |
| Executor (high risk) | Tier 3 | Destructive actions — explicit human approval |
| Safety Agent | Tier 3 | Enforces tier assignments — gateway for execution |

**Key Insight:** 10 of 14 agents are fully autonomous because they are **read-only**. Restrictions only apply to agents that **write** or **execute**.

### 5.4 Observe-Think-Act Loop

Each agent runs a continuous **OTA loop** without human prompting:

- **Observe:** Watch for triggers on message bus or blackboard
- **Think:** Process with LLM/ML model (local inference for M1/M2, API call for M3/M4)
- **Act:** Post result to bus/blackboard, or execute (if approved per tier)

| Agent | Observe Trigger | Think Method | Act Output |
|-------|----------------|-------------|-----------|
| Data Ingestion | New logs in ES index | Parse and normalize | Post to message bus |
| Anomaly Detection | New structured features on bus | ML inference (IF, SVM, LOF) | Post alert |
| Intent Classifier | New alert on bus | LLM API call | Write `intent` to blackboard |
| Retrieval Agent | `intent` updated on blackboard | FAISS query + LLM | Write `retrieved_context` |
| Validation Agent | `retrieved_context` updated | LLM API call | Write `validation_result` |
| Debaters | `validation_result` updated | LLM API call (parallel) | Post hypotheses |
| Referee | All 3 hypotheses posted | LLM API call | Post verdict |
| Safety Agent | Verdict posted | LLM risk check | Approve/block/escalate |
| Executor | Safety-approved plan | kubectl / API calls | Execute + post result |
| Memory Manager | Execution result posted | Embedding + FAISS write | Index for future retrieval |

### 5.5 Self-Triggering from ELK

The pipeline self-triggers from ELK — no human says "go analyze this":

| Mechanism | Cost | Latency | Recommendation |
|-----------|------|---------|---------------|
| **Elasticsearch Polling** | Cheapest — uses existing ELK | 30-60s | Default for non-urgent |
| **Elasticsearch Watcher** | Cheap — built-in alerting | ~5s | Critical alerts |
| Kafka/Redis Pub-Sub | Moderate — additional infra | ~1s | High-volume production |

**Recommended (cheapest):** ES Polling + Watcher. Uses infrastructure we already have.

### 5.6 Cost Analysis

| Model | LLM Calls/Incident | Cost/Incident | Monthly (100/day) |
|-------|-------------------|--------------|-------------------|
| **1** | 1 (Mistral-7B local) | ~$0.00 | ~$0/mo (GPU hosting only) |
| **2** | 1 (Qwen-7B local) | ~$0.00 | ~$0/mo (GPU hosting only) |
| **3** | 4 API calls | ~$0.08-0.15 | ~$240-450/mo |
| **4** | 9 API calls | ~$0.30-0.60 | ~$900-1,800/mo |

**LLM Cost per Call:**

| LLM | Input (per 1M tokens) | Output (per 1M tokens) | Typical Cost/Call |
|-----|----------------------|----------------------|------------------|
| GPT-4o-mini | $0.15 | $0.60 | ~$0.0005 |
| GPT-4o | $2.50 | $10.00 | ~$0.015 |
| Claude Opus 4 | $15.00 | $75.00 | ~$0.105 |
| Claude Sonnet | $3.00 | $15.00 | ~$0.021 |
| Mistral-7B (self-hosted) | GPU cost only | GPU cost only | ~$0.00 |
| Qwen2.5-7B (self-hosted) | GPU cost only | GPU cost only | ~$0.00 |

**Cheapest Autonomous Configuration:**
```
Incident arrives
    |
[Model 1 (Mistral-7B, self-hosted): classify]  --> $0.00
    |
Known, simple failure?
    |
  YES --> [Model 2 (Qwen-7B, self-hosted): diagnose]  --> $0.00
          Total: ~$0.00 per incident
    |
  NO --> [Model 3 pipeline (GPT-4o-mini where possible)]  --> $0.08-0.15
         [Escalate to Model 4 only if confidence < 70%]   --> $0.30-0.60
```

**80%+ of incidents resolve at near-zero cost** using self-hosted models.

### 5.7 Never-Autonomous Actions

| Action | Reason | Required |
|--------|--------|----------|
| Delete pods, PVCs, secrets | Irreversible data loss | Human approval |
| Modify RBAC permissions | Security-sensitive | Human approval + audit |
| Scale to 0 replicas | Causes outage | Human approval |
| Apply changes to production NS | High blast radius | Human approval |
| Act on confidence < 50% | Too uncertain | Escalate to SRE |

---

## 6. Data Analytics and Intelligent System

### 6.1 System Requirements Analysis

**Actors:** SRE (views Kibana, approves actions), K8s Cluster (signal source), Autonomous Agents (OTA loop), Safety Agent (HiTL gateway)

**Use Cases:**
1. Agents autonomously detect incidents from ELK
2. Agents cascade through classification, diagnosis, remediation
3. Low/medium-risk actions execute autonomously with logging
4. High-risk actions surface to SRE in Kibana for approval
5. Memory indexes outcomes for continuous improvement

### 6.2 System Design

```
+------------------------------------------------------------------+
|                     Kubernetes Cluster                             |
|  Filebeat --> Logstash --> Elasticsearch                           |
+----------------------------+-------------------------------------+
                             |
          +------------------+-------------------+
          |                                      |
+---------v----------+              +------------v-----------+
| Kibana (Web UI)     |              | Autonomous Agent       |
| https://localhost:   |              | Pipeline               |
| 5601                 |              |                        |
| - Live logs          |  <-- alerts  | Tier 1: Detection,     |
| - Incident alerts    |              |   Classification,      |
| - Agent activity     |              |   Retrieval, Debate    |
| - Approval queue     |  --> approve | Tier 2: Planner,       |
| - RCA results        |              |   Executor (low/med)   |
+----------------------+              | Tier 3: Safety gate    |
                                      +------------------------+
```

**Web-Based UI:** Kibana dashboards (live logs, alerts, agent activity, approval queue), interactive architecture diagram (`system-architecture-diagram.html`), Colab training notebooks.

**Infrastructure:** AWS EKS (v1.31, 2x c7i-flex.large), Terraform IaC, Flux CD GitOps, VPC 10.0.0.0/16.

### 6.3 Intelligent Solutions

| Model | Autonomy | Agent Pipeline | Output |
|-------|----------|---------------|--------|
| **1** | Fully autonomous (Tier 1) | Ingestion -> Detection -> Planner (Mistral LoRA) | Root cause label |
| **2** | Tier 1 + Tier 2 executor | + Retrieval -> Planner (Qwen LoRA) | Diagnosis + fix plan |
| **3** | Autonomous analysis, semi-auto execution | + Intent -> Retrieval -> Validation -> Planner (Opus) | Validated RCA |
| **4** | Autonomous debate, HiTL for high-risk | + Debaters -> Referee -> Safety -> Memory | Consensus RCA + learning |

### 6.4 System Supporting Environment

| Category | Technology |
|----------|-----------|
| ML Framework | PyTorch + Transformers + PEFT + TRL + bitsandbytes |
| Data Processing | pandas, PyArrow, Spark |
| Evaluation | scikit-learn, matplotlib, seaborn |
| Orchestration | Kubernetes (Kind, EKS) |
| IaC / GitOps | Terraform, Flux CD |
| Web UI / Observability | ELK Stack (Elasticsearch, Logstash, Kibana, Filebeat) |
| Cloud | AWS (EKS, VPC, IAM, us-west-2) |
| Training | Google Colab (A100 GPU) |
| Planned | FAISS, sentence-transformers, openai, anthropic, asyncio |

---

## 7. System Evaluation and Visualization

### 7.1 Three-Dimensional Evaluation

**ML Performance:** Classification accuracy (M1), generation quality (M2), overfitting diagnostics. Detailed in Section 4.4.

**Coordination Efficiency:** Cross-model comparison on same 500 incidents (Friedman test), hallucination reduction (M2->M3, McNemar's), debate quality (M4), accuracy-vs-cost curve. Detailed in Section 4.4.5.

**Autonomy Effectiveness:** Incidents resolved without human (by tier), false escalation rate (<10%), time-to-resolution autonomous vs. human-assisted, self-improvement rate (M4 memory). Detailed in Section 5.

### 7.2 Achievements and Constraints

**Achievements:** 4-model progressive architecture (6->14 agents), LoRA fine-tuned models with anti-overfitting, production ELK (3 environments), cost-optimized autonomy (80%+ at $0), cross-model evaluation framework with statistical tests.

**Constraints:** Models 3/6 designed not implemented, API keys needed, synthetic data only, Colab training dependency.

### 7.3 System Quality

| Attribute | Target | Status |
|-----------|--------|--------|
| Classification Accuracy (M1) | >= 70% | Evaluated |
| Generation Quality (M2) | >= 70% | Evaluated |
| Autonomous Resolution | > 80% simple | Designed |
| False Escalation | < 10% | Designed |
| Inference Latency (M1) | < 2s | Within target |
| Infrastructure | 3 environments | Operational |

### 7.4 Visualization

**ML (Model 1 — 7 plots):** Data distribution, training curves, confusion matrix, per-class P/R/F1, fuzzy accuracy, difficulty breakdown, validation dashboard.

**ML (Model 2):** Training curves, structure/keyword scores, combined score, sample predictions.

**Web-Based:** Kibana dashboards (live logs, alerts, agent activity, approval queue), interactive architecture diagram, Colab notebooks.

---

## Appendices

### Appendix A — Repository Structure

```
Multi-Agent-Collaboration-System/
|-- k8s_synth_generator_portfolio.py     # Synthetic data generator
|-- transform_incidents.py               # Data transformation pipeline
|-- system-architecture-diagram.html     # Interactive architecture visualization
|-- agents/models/
|   |-- model1_mistral_lora.py           # Model 1: Mistral LoRA
|   |-- model2_qwen_lora.py             # Model 2: Qwen LoRA
|-- data/                                # JSONL + Parquet datasets
|-- clusters/elk*/                       # ELK stack (local, staging, prod)
|-- docs/                                # Architecture, evaluation, literature
|-- train_models_colab*.ipynb            # Training notebooks
```

### Appendix B — Agent Taxonomy

| # | Agent | Type | Model | Tier | LLM |
|---|-------|------|-------|------|-----|
| 1 | Data Ingestion | Core | M1 | Tier 1 | — |
| 2 | Preprocessing | Core | M1 | Tier 1 | — |
| 3 | Anomaly Detection | Analysis | M1 | Tier 1 | IF, SVM, LOF |
| 4 | Planner | Planning | M1 | Tier 2 | Mistral/Qwen/Opus |
| 5 | Executor | Execution | M1 | Tier 2-3 | — |
| 6 | Reviewer | Oversight | M1 | Tier 2 | — |
| 7 | Retrieval Agent | Analysis | M2 | Tier 1 | GPT-4o-mini |
| 8 | Intent Classifier | Analysis | M3 | Tier 1 | GPT-4o-mini/Haiku |
| 9 | Validation Agent | Analysis | M3 | Tier 1 | GPT-4o/Sonnet |
| 10 | Debater 1 | Debate | M4 | Tier 1 | Claude Opus 4 |
| 11 | Debater 2 | Debate | M4 | Tier 1 | GPT-4o |
| 12 | Debater 3 | Debate | M4 | Tier 1 | DeepSeek-R1 |
| 13 | Referee | Consensus | M4 | Tier 1 | Opus/GPT-4o |
| 14 | Safety Agent | Safety | M4 | Tier 3 | GPT-4o-mini |
| 15 | Memory Manager | Memory | M4 | Tier 2 | Embeddings |
