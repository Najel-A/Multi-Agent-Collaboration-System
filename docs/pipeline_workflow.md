# Pipeline Workflow Guide

## How to Understand the End-to-End System

This document walks through the complete data pipeline of the Multi-Agent Collaboration System, step by step. Each step explains **what happens**, **why it happens**, **what goes in**, **what comes out**, and **where to find it in the codebase**. Read this top to bottom to follow an incident from generation to resolution.

> Reference: `docs/system_architecture.md` for full architectural details.

---

## Step 0: The Problem

A Kubernetes pod is failing. It could be an OOM kill, a missing secret, an image pull error, a scheduling failure, or any of 18 different root causes. An SRE needs to know: **what went wrong, why, and how to fix it.**

The system automates this entire workflow using 15 specialized agents coordinated across 4 progressive models.

---

## Step 1: Generate Synthetic K8s Incidents

**What happens:** A Python script programmatically creates realistic Kubernetes failure incidents with full observability signals.

**Why:** Real production incidents are sensitive, inconsistent, and hard to label. Synthetic generation gives us a balanced, fully-labeled, reproducible dataset for training and evaluation.

```
 INPUT:  Configuration (18 scenarios, 500 per scenario, seed=7)
         |
         v
 SCRIPT: k8s_synth_generator_portfolio.py
         |
         v
OUTPUT:  data/synthetic_source.jsonl  (8,502 incidents, ~40 MB)
         data/stats.json              (per-scenario counts)
```

**What's inside each incident:**

| Field | Example | Purpose |
|-------|---------|---------|
| `context.namespace` | `backend-prod` | Where the failure occurred |
| `context.workload_name` | `api-server-abc12` | Which workload failed |
| `fault.scenario_id` | `crashloop_oomkilled_limit_too_low` | Ground truth root cause |
| `observations.kubectl_describe_pod` | `Status: Running\n Reason: CrashLoopBackOff\n Exit Code: 137` | What kubectl shows |
| `observations.container_logs` | `java.lang.OutOfMemoryError: Java heap space` | Application logs |
| `observations.metrics_snapshot` | `{"restarts": 14, "oom_killed": true}` | Metrics data |
| `remediation.diagnosis` | `Container OOM-killed due to memory limit too low` | How to explain it |
| `remediation.fix_plan` | `["Increase memory limits", "Check for leaks"]` | How to fix it |

**Run it:**
```bash
python k8s_synth_generator_portfolio.py --per_scenario 500 --outdir ./data --seed 7
```

**Where in the architecture:** Section 3.2 (Data Collection)

---

## Step 2: Transform Raw Data into Agent-Specific Datasets

**What happens:** The raw JSONL is split and reshaped into two Parquet files, each optimized for a different type of agent.

**Why:** Agent 1 (classification) needs tabular features. Agent 2 (generation) needs text pairs. One raw format cannot serve both efficiently.

```
 INPUT:  data/synthetic_source.jsonl
         |
         v
 SCRIPT: transform_incidents.py --input data/synthetic_source.jsonl --outdir data/processed
         |
         +--> data/processed/agent1_structured.parquet  (27 columns, tabular)
         |
         +--> data/processed/agent2_evidence.parquet    (16 columns, text pairs)
```

**What the transformation does:**

| Step | Operation | Output Fields |
|------|-----------|---------------|
| 1 | Flatten nested JSON | All dot-separated paths (e.g., `context.namespace`) |
| 2 | Regex parse `kubectl_describe_pod` | `pod_status`, `waiting_reason`, `error_message`, `restart_count` |
| 3 | Parse `kubectl_get_events` table | `event_type`, `event_reason`, `event_message` |
| 4 | Extract `metrics_snapshot` | `restart_count_metrics`, `oom_killed` |
| 5 | Derive `root_cause_family` | Map scenario_id to 12 coarse labels (oom, rbac, dns, etc.) |
| 6 | Derive `symptom_family` | Map pod_status/waiting_reason to observable symptoms |
| 7 | Build `evidence_text` (Agent 2) | Concatenate all kubectl output + logs + metrics into one string |
| 8 | Extract remediation text (Agent 2) | `diagnosis_text`, `fix_plan_text`, `actions_text`, `verification_text`, `rollback_text` |

**Agent 1 gets:** 27 structured columns for classification (pod_status = "CrashLoopBackOff", restart_count = 14, oom_killed = true -> label: "oom")

**Agent 2 gets:** Raw evidence text paired with target remediation text for generation training.

**Run it:**
```bash
python transform_incidents.py --input data/synthetic_source.jsonl --outdir data/processed
```

**Where in the architecture:** Section 3.3-3.4 (Pre-processing, Transformation)

---

## Step 3: Train Agent Models (LoRA Fine-Tuning)

**What happens:** Two LLMs are fine-tuned with LoRA adapters on the agent-specific datasets.

**Why:** Pre-trained LLMs don't know about K8s incident patterns. LoRA fine-tuning teaches them domain-specific classification and generation at a fraction of the cost of full fine-tuning (~0.1% of parameters).

```
 INPUT:  data/processed/agent1_structured.parquet
         |
         v
 SCRIPT: python -m agents.models.model1_mistral_lora --input data/processed/agent1_structured.parquet
         |
         v
OUTPUT:  agents/models/trained/model1_mistral/lora_adapter/    (~130 MB)
         agents/models/trained/model1_mistral/eval_results.json
         agents/models/trained/model1_mistral/plots/           (7 eval plots)


 INPUT:  data/processed/agent2_evidence.parquet
         |
         v
 SCRIPT: python -m agents.models.model2_qwen_lora --input data/processed/agent2_evidence.parquet
         |
         v
OUTPUT:  agents/models/trained/model2_qwen/lora_adapter/       (~130 MB)
         agents/models/trained/model2_qwen/eval_results.json
```

**What each model learns:**

| Model | Base LLM | Input | Output | Target |
|-------|----------|-------|--------|--------|
| **Model 1** | Mistral-7B-Instruct-v0.3 | Structured signals (pod_status, events, restart_count...) | Root cause label (e.g., `oom`, `image_pull`) | >= 70% fuzzy accuracy |
| **Model 2** | Qwen2.5-7B-Instruct | Raw evidence text (kubectl output, logs, metrics) | Diagnosis + Fix Plan + Actions + Verification + Rollback | >= 70% combined score |

**Training configuration:**

| Setting | Model 1 | Model 2 |
|---------|---------|---------|
| LoRA rank | 16 | 16 |
| LoRA alpha | 32 | 32 |
| LoRA dropout | 0.05 | 0.05 |
| Quantization | 4-bit NF4 | 4-bit NF4 |
| Max tokens | 512 | 1024 |
| Format | Mistral instruct | ChatML |
| LR | 2e-4 (cosine) | 2e-4 (cosine) |
| Train/test | 80/20 | 80/20 |

**Run on Colab:** Open `train_models_colab.ipynb` or `train_models_colab_v2.ipynb` on Google Colab with A100 runtime.

**Where in the architecture:** Section 4.1 (Model 1 and Model 2 details)

---

## Step 4: Agent Inference — Incident Flows Through the Multi-Agent Pipeline

**What happens:** When an incident arrives, it flows through a chain of specialized agents. The system offers 4 coordination models of increasing sophistication. Each model inherits agents from the previous one.

**Why:** Different incidents need different levels of analysis. Simple failures (image pull, config error) resolve cheaply with Model 1. Complex/novel failures benefit from the deeper reasoning of Models 3-4.

### Model 1 Flow: Sequential Message Passing (6 agents, ~$0)

```
K8s pod fails
    |
    v
[Data Ingestion Agent]        Collects logs from ELK/kubectl
    | message
    v
[Preprocessing Agent]         Parses, normalizes, validates
    | message
    v
[Anomaly Detection Agent]     Scores with IF/SVM/LOF, flags anomaly
    | message
    v
[Planner Agent]               Mistral-7B LoRA classifies root cause
    | message                  Input: structured signals
    v                          Output: "oom" (or 1 of 12 labels)
[Executor Agent]              Runs remediation kubectl commands
    | message
    v
[Reviewer Agent]              Verifies fix, triggers rollback if needed
    |
    v
DONE: Root cause classified
```

### Model 2 Flow: + Shared Knowledge via RAG (7 agents, ~$0)

```
...same as M1 through Anomaly Detection...
    |
    v
[Retrieval Agent]  (NEW)      Queries FAISS index:
    |                          "What past incidents look like this?"
    | retrieved context
    v
[Planner Agent]               Qwen2.5-7B LoRA generates diagnosis
    |                          Input: raw evidence + RAG context
    v                          Output: Diagnosis, Fix Plan, Actions,
                                       Verification, Rollback
[Executor Agent] --> [Reviewer Agent]
    |
    v
DONE: Root cause + full remediation plan
```

### Model 3 Flow: + Blackboard & Validation (9 agents, ~$0.08-0.15)

```
...same as M2 through Preprocessing...
    |
    v
+-------- BLACKBOARD (shared state) --------+
|                                            |
|  [Intent Classifier]  (NEW)                |
|    Reads: raw incident                     |
|    Writes: intent (type, severity, blast)  |
|         |                                  |
|  [Retrieval Agent]                         |
|    Reads: intent                           |
|    Writes: retrieved_context               |
|         |                                  |
|  [Validation Agent]  (NEW)                 |
|    Reads: intent + retrieved_context       |
|    Writes: validation_result               |
|    (cross-checks, hallucination flags)     |
|         |                                  |
|  [Planner]  (Claude Opus 4 / GPT-4o)      |
|    Reads: ALL blackboard state             |
|    Writes: rca_plan                        |
|                                            |
+--------------------------------------------+
    |
    v
[Executor Agent] --> [Reviewer Agent]
    |
    v
DONE: Validated, auditable RCA with decision trail
```

### Model 4 Flow: + Debate, Safety & Memory (14 agents, ~$0.30-0.60)

```
...Model 3 runs first, produces blackboard state...
    |
    v
[Model 3 Blackboard Output]
    |
    +---------------+---------------+
    |               |               |
[Debater 1]    [Debater 2]    [Debater 3]
 Claude Opus    GPT-4o         DeepSeek-R1
    |               |               |
    +--- each proposes a hypothesis ---+
    |               |               |
    +---- cross-examination round ----+
    |
    v
[Referee Agent]
    Scores by: evidence (35%), logic (25%),
    specificity (20%), resilience (20%)
    Selects winner (NOT majority vote)
    |
    v
[Safety Agent (HiTL)]
    Low risk: auto-approve
    Medium risk: 30s delay for override
    High risk: BLOCKS until human approves in Kibana
    |
    v
[Executor Agent] --> [Reviewer Agent]
    |
    v
[Context Memory Manager]
    Indexes: incident, hypotheses, winner, outcome
    Future incidents benefit from: "Last 3 times
    we saw this pattern, the cause was X"
    |
    v
DONE: Consensus RCA + safety-gated execution + learning
```

**Where in the architecture:** Section 4.1 (all 4 models), Section 4.2 (inheritance)

---

## Step 5: Autonomous Operation — How Agents Self-Trigger

**What happens:** Agents run continuously in an Observe-Think-Act loop. The pipeline self-triggers when new incidents appear in ELK — no human prompts the system.

**Why:** A system that requires a human to say "go analyze this" isn't useful for 24/7 operations.

```
[K8s pods fail]
    |
[Filebeat ships logs to Logstash --> Elasticsearch]
    |
[Data Ingestion Agent: polls ES every 30s for new anomaly patterns]
    |
    v
[Autonomous cascade begins — each agent triggers the next]
    |
    v
[Executor: checks autonomy tier before acting]
    |
    +-- Tier 1 (read-only agents): fully autonomous, no delay
    +-- Tier 2 (low/med risk): executes after 30s human-override window
    +-- Tier 3 (high risk): WAITS for human approval in Kibana dashboard
```

**Cost-optimized routing:**
```
Incident arrives
    |
[M1 classifies root cause]  --> $0.00
    |
Known, simple failure?
    |
YES --> [M2 generates diagnosis]  --> $0.00 (80%+ of incidents)
    |
NO  --> [M3 validates with blackboard]  --> $0.08-0.15
    |
    Still uncertain (confidence < 70%)?
    |
YES --> [M4 debate for consensus]  --> $0.30-0.60
```

**Where in the architecture:** Section 5 (Agent Autonomy)

---

## Step 6: Infrastructure — Where It All Runs

**What happens:** The ELK stack collects, indexes, and visualizes logs. K8s clusters host the agents and observability pipeline.

```
+------------------------------------------------------------------+
|                     Kubernetes Cluster                             |
|                                                                   |
|  [Filebeat]  --> [Logstash 8.14]  --> [Elasticsearch 8.14]        |
|  DaemonSet       Parse & normalize     Index & search             |
|  Ships logs                                 |                     |
|                                         [Kibana 8.14]             |
|                                         Live logs, alerts,        |
|                                         agent activity,           |
|                                         approval queue            |
+------------------------------------------------------------------+

3 deployment environments:

  Local Dev:      Kind (Docker)              clusters/elk/
  AWS Staging:    EKS + Flux CD GitOps       clusters/elk-aws/
  AWS Production: EKS + Terraform            clusters/elk-prod/
                  v1.31, 2x c7i-flex.large   infrastructure.tf
                  VPC 10.0.0.0/16
```

**Set up local environment:**
```bash
kind create cluster --name dev
cd clusters/elk
make
make apply
make reconcile
kubectl port-forward svc/kibana-sample-kb-http 5601:5601 -n elastic-system
# Open https://localhost:5601
```

**Where in the architecture:** Section 6.2 (System Design)

---

## Step 7: Evaluate — Measure How Well It Works

**What happens:** Each model is evaluated with metrics tailored to its specific task. All 4 models are compared on the same 500 held-out incidents.

### Model 1 Evaluation (Classification)

```
[500 held-out samples] --> [Mistral-7B inference (greedy)] --> [Compare to ground truth]
                                                                      |
                                                        +-------------+-------------+
                                                        |             |             |
                                                  Fuzzy Accuracy  Confusion    Per-Class
                                                  (target >=70%)   Matrix      P/R/F1
```

**7 evaluation plots generated:**
1. Data distribution (root cause + difficulty)
2. Training curves (per-step + per-epoch with overfitting gap)
3. Confusion matrix heatmap
4. Per-class precision/recall/F1
5. Per-class fuzzy accuracy with 70% target line
6. Accuracy by difficulty (easy/medium/hard)
7. Validation summary dashboard

### Model 2 Evaluation (Generation)

```
[200 held-out samples] --> [Qwen2.5-7B inference (greedy)] --> [Score output]
                                                                    |
                                                      +-------------+----------+
                                                      |                        |
                                                Structure Score (40%)    Keyword Overlap (60%)
                                                sections present?        technical terms matched?
                                                      |                        |
                                                      +---- Combined Score ----+
                                                             (target >= 70%)
```

### Cross-Model Comparison

```
Same 500 incidents --> [M1] --> accuracy_1
                   --> [M2] --> accuracy_2
                   --> [M3] --> accuracy_3  (hallucination rate, tool-call accuracy)
                   --> [M4] --> accuracy_4  (debate diversity, error-repeat reduction)
                                    |
                              Friedman test (are models significantly different?)
                              McNemar's test (pairwise: M2 vs M3, M3 vs M4)
                              Accuracy vs. cost efficiency curve
```

**Where in the architecture:** Section 4.4 (Evaluation Methods), Section 7 (System Evaluation)

---

## Complete Pipeline — One Diagram

```
STEP 1                    STEP 2                     STEP 3
Generate                  Transform                  Train

k8s_synth_generator  -->  transform_incidents.py -->  Model 1: Mistral LoRA
  |                         |           |             Model 2: Qwen LoRA
  v                         v           v                  |
synthetic_source     agent1_structured  agent2_evidence    v
.jsonl (8,502)       .parquet (27 col)  .parquet (16 col)  LoRA adapters

                              |                            |
                              v                            v

STEP 4                                     STEP 5
Agent Inference                            Autonomous Operation

M1: Detect --> Classify (Mistral)          Agents self-trigger from ELK
M2: + Retrieve (FAISS) --> Generate (Qwen) 80%+ incidents at $0
M3: + Blackboard --> Validate --> Plan     Complex incidents escalate to M3/M4
M4: + Debate --> Referee --> Safety         High-risk actions need human approval
       |                                        |
       v                                        v

STEP 6                                     STEP 7
Infrastructure                             Evaluate

Filebeat --> Logstash --> ES --> Kibana     M1: fuzzy acc, confusion matrix, F1
3 environments: local, staging, prod       M2: structure + keyword = combined
EKS v1.31 + Terraform                     M3: trajectory, hallucination detection
                                           M4: debate diversity, error-repeat, safety
                                           Cross-model: Friedman test, cost curve
```

---

## Quick Reference — Key Files

| Step | File | Purpose |
|------|------|---------|
| 1 | `k8s_synth_generator_portfolio.py` | Generate 8,502 synthetic incidents |
| 2 | `transform_incidents.py` | Transform JSONL to 2 Parquet datasets |
| 3a | `agents/models/model1_mistral_lora.py` | Train Mistral-7B LoRA (classification) |
| 3b | `agents/models/model2_qwen_lora.py` | Train Qwen2.5-7B LoRA (generation) |
| 3 | `train_models_colab.ipynb` | Colab notebook for training both models |
| 4 | `docs/system_architecture.md` Section 4.1 | Full agent pipeline for all 4 models |
| 5 | `docs/system_architecture.md` Section 5 | Autonomy tiers, OTA loop, cost analysis |
| 6a | `clusters/elk/` | Local Kind cluster + ELK stack |
| 6b | `clusters/elk-prod/infrastructure.tf` | AWS production Terraform |
| 7 | `docs/report_4.4_4.5_evaluation.md` | Evaluation methods and result templates |

---

## Quick Reference — Agent Roster (15 agents)

| # | Agent | Introduced | Autonomy | LLM | What It Does |
|---|-------|-----------|----------|-----|-------------|
| 1 | Data Ingestion | M1 | Tier 1 | — | Collects logs from ELK |
| 2 | Preprocessing | M1 | Tier 1 | — | Parses, normalizes, validates |
| 3 | Anomaly Detection | M1 | Tier 1 | IF/SVM/LOF | Flags anomalies |
| 4 | Planner | M1 | Tier 2 | Mistral/Qwen/Opus | Classifies or generates RCA |
| 5 | Executor | M1 | Tier 2-3 | — | Runs kubectl commands |
| 6 | Reviewer | M1 | Tier 2 | — | Verifies fix, triggers rollback |
| 7 | Retrieval Agent | M2 | Tier 1 | GPT-4o-mini | FAISS query for similar incidents |
| 8 | Intent Classifier | M3 | Tier 1 | GPT-4o-mini/Haiku | Categorizes anomaly type/severity |
| 9 | Validation Agent | M3 | Tier 1 | GPT-4o/Sonnet | Detects hallucinations |
| 10 | Debater 1 | M4 | Tier 1 | Claude Opus 4 | Analytical hypothesis |
| 11 | Debater 2 | M4 | Tier 1 | GPT-4o | Planning-focused hypothesis |
| 12 | Debater 3 | M4 | Tier 1 | DeepSeek-R1 | Contrarian hypothesis |
| 13 | Referee | M4 | Tier 1 | Opus/GPT-4o | Scores arguments, picks winner |
| 14 | Safety Agent | M4 | Tier 3 | GPT-4o-mini | Blocks high-risk actions |
| 15 | Memory Manager | M4 | Tier 2 | Embeddings | Indexes outcomes for learning |
