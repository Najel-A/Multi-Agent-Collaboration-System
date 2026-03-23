# Multi-Agent Collaboration System for Kubernetes Root Cause Analysis

**DATA 298A/B MSDA Project — San Jose State University**
Team 1: Akash Thiagarajan, Chelsea Jaculina, Devesh Singh, Mrunali Katta, Najel Alarcon

---

## The Problem: Kubernetes Incident Coordination

Cloud-native infrastructure on Kubernetes generates massive operational signals — pod statuses, container logs, cluster events, resource metrics. When failures occur, SREs must manually sift through these signals to identify root causes.

**Core Challenge:** No single agent has the breadth of knowledge to handle the full spectrum of cloud incidents alone.

**Our Solution:** 4 progressive multi-agent architectures that enable specialized autonomous agents to collaboratively resolve complex Kubernetes incidents.

| Model | Coordination Capability | Key Addition |
|-------|------------------------|-------------|
| Model 1 | Sequential message passing | Planner, Executor, Reviewer |
| Model 2 | Shared knowledge (RAG) | Retrieval Agent + FAISS |
| Model 3 | Blackboard + multi-step validation | Intent Classifier, Validation Agent |
| Model 4 | Debate + referee + safety + memory | Debaters, Referee, Safety Agent |

Each model includes a **baseline (zero-shot)** and **fine-tuned** evaluation to quantify the impact of LoRA training.

---

## System Architecture Overview

### How Models Build on Each Other

```
Model 1 (6 agents):  Ingestion -> Preprocessing -> Detection -> Planner -> Executor -> Reviewer
                     Sequential messages | Qwen2.5-7B LoRA

Model 2 (7 agents):  + Retrieval Agent + Knowledge Base
                     Shared memory (RAG) | Mistral-7B LoRA

Model 3 (9 agents):  + Intent Classifier + Validation Agent + Blackboard
                     Multi-step validation | Opus / GPT-4o

Model 4 (14 agents): + 3 Debaters + Referee + Safety + Memory Manager
                     Debate consensus + HiTL + self-improvement
```

### Model Comparison

| Model | Coordination | ML Approach | Strengths | Limitations |
|-------|-------------|------------|-----------|-------------|
| **1** | Sequential pipeline | Qwen2.5-7B LoRA (classification + generation) | Fast, strong base model, interpretable | No shared knowledge |
| **2** | Pipeline + shared memory | Mistral-7B LoRA (generation) + RAG | Historical matching, retrieval-augmented | No validation |
| **3** | Blackboard + validation | Opus / GPT-4o (reasoning) | Auditable, hallucination-checked | Expensive per-call |
| **4** | Debate + referee + safety | Heterogeneous LLMs (debate) | Robust, self-improving, safe | Token-intensive |

---

## Model 1 Architecture: Centralized Linear Pipeline

**Coordination: Sequential Message Passing | Status: Implemented**

```
Data Ingestion --> Preprocessing --> Anomaly Detection --> Planner --> Executor --> Reviewer
       |                |                  |                 |            |           |
    [message]       [message]          [message]        [message]    [message]   [message]
```

### Agent Roles

| Agent | Responsibility |
|-------|---------------|
| Data Ingestion Agent | Collect and normalize raw K8s logs |
| Preprocessing Agent | Parse, enrich, validate log data; generate log templates |
| Anomaly Detection Agent | Identify outliers using Isolation Forest, One-Class SVM, LOF |
| Planner Agent | Choose playbook/action based on alert; classify root cause |
| Executor Agent | Execute remediation steps (kubectl commands, API calls) |
| Reviewer Agent | Verify outcome post-remediation; trigger rollback if needed |

**ML Model:** Qwen2.5-7B-Instruct + LoRA (4-bit NF4 quantization, ~1% trainable params)

---

## Model 2 Architecture: RAG-Enhanced Pipeline

**Builds on Model 1 | New: Shared Memory via RAG | Status: Implemented**

```
Data Ingestion --> Preprocessing --> Anomaly Detection --> Retrieval Agent
                                                               |
                                            +---------[Knowledge Base]--------+
                                            |        (FAISS / Embeddings)     |
                                    Planner (with RAG context) --> Executor --> Reviewer
```

### What's New
- **Retrieval Agent**: Queries FAISS vector store for similar past incidents, fetches runbooks, configs, and metrics
- **Knowledge Base**: FAISS embeddings of historical incidents for semantic similarity search
- **Enhanced Planner**: Now reasons with historical context from RAG retrieval

**ML Model:** Mistral-7B-Instruct-v0.3 + LoRA — generates structured diagnosis + remediation plans, augmented with RAG retrieval

---

## Model 3 & 4 Architectures: Blackboard + Debate

### Model 3: SMART-Inspired Blackboard Architecture (Designed)

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
              Intent         Retrieval  Validation  Planner  Executor
              Classifier     Agent      Agent
```

### Model 4: Self-Evolving Cognitive Hybrid MAS (Designed)

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

**14 total agents** | Debate scoring: 35% evidence grounding, 25% logic, 20% remediation specificity, 20% counter-argument resilience

---

## Machine Learning Model & Evaluation Results

### Model 1: Qwen2.5-7B-Instruct — Sequential Pipeline (n=400)

#### Qwen2.5-7B Baseline (Zero-Shot, No Fine-Tuning)

| Metric | Baseline Value |
|--------|---------------|
| **Scenario Match Accuracy** | **42.0%** |
| Section Score (structure) | 0.683 |
| Keyword Recall | 0.271 |
| BERTScore F1 | **0.773** |
| ROUGE-1 F1 | 0.298 |
| ROUGE-2 F1 | 0.103 |
| ROUGE-L F1 | 0.194 |
| BLEU | 0.063 |

| Scenario | Baseline | | Scenario | Baseline |
|----------|----------|-|----------|----------|
| createcontainerconfigerror_bad_configmap_key | **78.0%** | | quota_exceeded_pods | **78.0%** |
| oomkilled_limit_too_low | **74.0%** | | createcontainerconfigerror_missing_secret | **72.0%** |
| liveness_probe_failure | **68.0%** | | pvc_not_found_mountfail | **66.0%** |
| rbac_forbidden | **60.9%** | | pvc_pending_missing_storageclass | **55.2%** |
| crashloop_bad_args | **40.0%** | | failedscheduling_taint | **37.5%** |
| imagepull_bad_tag | **34.0%** | | readiness_probe_failure | **29.2%** |
| imagepull_registry_auth | **17.9%** | | service_connection_refused | **15.9%** |
| failedscheduling_insufficient_memory | **13.3%** | | nodeselector_mismatch | **7.5%** |
| failedscheduling_insufficient_cpu | **5.2%** | | | |

**0 of 17 scenarios at 100%. Only 6 of 17 achieved >= 60%.**

#### Qwen2.5-7B Fine-Tuned (LoRA)

| Metric | Trained Value | Improvement |
|--------|--------------|-------------|
| **Scenario Match Accuracy** | **71.5%** | **+29.5 pp** |
| Section Score (structure) | 0.972 | +0.289 |
| Keyword Recall | 0.419 | +0.148 |
| BERTScore F1 | **0.845** | **+0.072** |
| ROUGE-1 F1 | 0.430 | +0.132 |
| ROUGE-2 F1 | 0.173 | +0.070 |
| ROUGE-L F1 | 0.281 | +0.087 |
| BLEU | 0.118 | +0.055 |
| Trainable Params | ~1-2% of 7B | — |

| BERTScore Component | Value |
|---------------------|-------|
| Precision | 0.827 |
| Recall | 0.863 |
| **F1** | **0.845** |

| Scenario | Trained | Δ vs Baseline | | Scenario | Trained | Δ vs Baseline |
|----------|---------|---------------|-|----------|---------|---------------|
| createcontainerconfigerror_bad_configmap_key | **100.0%** | +22.0 | | pvc_not_found_mountfail | **100.0%** | +34.0 |
| oomkilled_limit_too_low | **100.0%** | +26.0 | | liveness_probe_failure | **100.0%** | +32.0 |
| createcontainerconfigerror_missing_secret | **100.0%** | +28.0 | | quota_exceeded_pods | **100.0%** | +22.0 |
| rbac_forbidden | **91.3%** | +30.4 | | pvc_pending_missing_storageclass | **86.2%** | +31.0 |
| crashloop_bad_args | **75.0%** | +35.0 | | failedscheduling_taint | **75.0%** | +37.5 |
| imagepull_bad_tag | **64.0%** | +30.0 | | readiness_probe_failure | **58.3%** | +29.1 |
| failedscheduling_insufficient_memory | **46.7%** | +33.4 | | imagepull_registry_auth | **46.4%** | +28.5 |
| service_connection_refused | **45.5%** | +29.6 | | nodeselector_mismatch | **25.0%** | +17.5 |
| failedscheduling_insufficient_cpu | **24.1%** | +18.9 | | | | |

**6 of 17 scenarios at 100% (up from 0). 10 of 17 achieved >= 58%.**

#### Model 1 Analysis
- **LoRA fine-tuning adds +29.5 pp** scenario accuracy with only ~1% trainable parameters
- **Section structure jumps from 0.683 → 0.972** — fine-tuning teaches the model to consistently produce Diagnosis / Fix Plan / Verification sections
- **Largest per-scenario gains** on failedscheduling_taint (+37.5 pp) and crashloop_bad_args (+35.0 pp) — the model learns to distinguish ambiguous failure modes
- **BERTScore >> BLEU/ROUGE** gap confirms the model produces semantically correct but lexically diverse outputs — it paraphrases rather than copies

---

### Model 2: Mistral-7B-Instruct-v0.3 — RAG-Enhanced Pipeline (n=400)

| Parameter | Value |
|-----------|-------|
| Base Model | Mistral-7B-Instruct-v0.3 |
| LoRA Config | Rank 32, Alpha 64, Dropout 0.1 |
| Max Sequence Length | 1024 tokens |
| Early Stopping | Patience = 2 epochs |
| RAG | FAISS vector store, top-3 similar incidents |

#### Mistral-7B Baseline (Zero-Shot, No Fine-Tuning)

| Metric | Baseline Value |
|--------|---------------|
| **Scenario Match Accuracy** | **33.0%** |
| Section Score (structure) | 0.581 |
| Keyword Recall | 0.209 |
| BERTScore F1 | **0.724** |
| ROUGE-1 F1 | 0.241 |
| ROUGE-2 F1 | 0.072 |
| ROUGE-L F1 | 0.153 |
| BLEU | 0.038 |

| Scenario | Baseline | | Scenario | Baseline |
|----------|----------|-|----------|----------|
| createcontainerconfigerror_bad_configmap_key | **68.0%** | | quota_exceeded_pods | **66.0%** |
| oomkilled_limit_too_low | **62.0%** | | createcontainerconfigerror_missing_secret | **60.0%** |
| liveness_probe_failure | **55.0%** | | pvc_not_found_mountfail | **53.0%** |
| rbac_forbidden | **43.5%** | | pvc_pending_missing_storageclass | **39.7%** |
| crashloop_bad_args | **27.5%** | | failedscheduling_taint | **25.0%** |
| imagepull_bad_tag | **22.0%** | | readiness_probe_failure | **16.7%** |
| imagepull_registry_auth | **10.7%** | | service_connection_refused | **9.1%** |
| failedscheduling_insufficient_memory | **6.7%** | | nodeselector_mismatch | **2.5%** |
| failedscheduling_insufficient_cpu | **1.7%** | | | |

**0 of 17 scenarios at 100%. Only 2 of 17 achieved >= 60%.**

#### Mistral-7B Fine-Tuned (LoRA + RAG)

| Metric | Trained Value | Improvement | vs Model 1 |
|--------|--------------|-------------|------------|
| **Scenario Match Accuracy** | **68.8%** | **+35.8 pp** | −2.7 pp |
| Section Score (structure) | 0.953 | +0.372 | −0.019 |
| Keyword Recall | 0.487 | +0.278 | +0.068 |
| BERTScore F1 | **0.831** | **+0.107** | −0.014 |
| ROUGE-1 F1 | 0.462 | +0.221 | +0.032 |
| ROUGE-2 F1 | 0.193 | +0.121 | +0.020 |
| ROUGE-L F1 | 0.302 | +0.149 | +0.021 |
| BLEU | 0.142 | +0.104 | +0.024 |
| Trainable Params | ~1-2% of 7B | — | — |

| BERTScore Component | Value |
|---------------------|-------|
| Precision | 0.815 |
| Recall | 0.848 |
| **F1** | **0.831** |

| Scenario | Trained | Δ vs Baseline | | Scenario | Trained | Δ vs Baseline |
|----------|---------|---------------|-|----------|---------|---------------|
| createcontainerconfigerror_bad_configmap_key | **100.0%** | +32.0 | | pvc_not_found_mountfail | **100.0%** | +47.0 |
| oomkilled_limit_too_low | **100.0%** | +38.0 | | liveness_probe_failure | **100.0%** | +45.0 |
| createcontainerconfigerror_missing_secret | **100.0%** | +40.0 | | quota_exceeded_pods | **100.0%** | +34.0 |
| rbac_forbidden | **87.0%** | +43.5 | | pvc_pending_missing_storageclass | **82.8%** | +43.1 |
| crashloop_bad_args | **72.5%** | +45.0 | | failedscheduling_taint | **72.5%** | +47.5 |
| imagepull_bad_tag | **60.0%** | +38.0 | | readiness_probe_failure | **54.2%** | +37.5 |
| imagepull_registry_auth | **46.4%** | +35.7 | | failedscheduling_insufficient_memory | **43.3%** | +36.6 |
| service_connection_refused | **40.9%** | +31.8 | | nodeselector_mismatch | **20.0%** | +17.5 |
| failedscheduling_insufficient_cpu | **17.2%** | +15.5 | | | | |

**6 of 17 scenarios at 100% (up from 0). 8 of 17 achieved >= 54%.**

#### Model 2 Analysis
- **LoRA + RAG adds +35.8 pp** over Mistral's weaker zero-shot baseline — the largest absolute improvement of any model, driven by RAG compensating for Mistral's lower base reasoning capacity (MMLU 62.5 vs Qwen's 74.2)
- **RAG boosts keyword recall** (+0.278 vs baseline, and +0.068 vs Model 1) — retrieved historical incidents inject domain-specific terminology into the generation
- **ROUGE scores exceed Model 1** despite lower accuracy — RAG-retrieved context provides lexically similar reference material, improving surface-level overlap
- **Scenario accuracy (68.8%) approaches but does not exceed Model 1 (71.5%)** — Mistral's weaker base limits how effectively it leverages RAG context for classification
- **Low-accuracy scenarios remain challenging**: CPU scheduling (17.2%) and node selector (20.0%) — even with retrieved similar incidents, these resource-contention signals are inherently ambiguous

**Evaluation Formula:**
```
Combined Score = 0.4 x Structure Score + 0.6 x Keyword Overlap

Structure Score = (sections found) / 3
  Checks for: "Diagnosis", "Fix Plan", "Verification"

Keyword Overlap = |GT_keywords ∩ Pred_keywords| / |GT_keywords|
  Only words > 4 chars — measures technical concept coverage
```

**Why not BLEU/ROUGE?** They penalize valid paraphrases. Our metrics mirror how an SRE evaluates: "Is it structured? Does it mention the right things?"

---

## Comparative ML Model Results

| Metric | Qwen Baseline | Model 1 (Qwen + LoRA) | Mistral Baseline | Model 2 (Mistral + LoRA + RAG) |
|--------|--------------|----------------------|-----------------|-------------------------------|
| **Scenario Match Accuracy** | 42.0% | **71.5%** (+29.5 pp) | 33.0% | **68.8%** (+35.8 pp) |
| **BERTScore F1** | 0.773 | **0.845** (+0.072) | 0.724 | **0.831** (+0.107) |
| **Section Score** | 0.683 | **0.972** (+0.289) | 0.581 | **0.953** (+0.372) |
| **Keyword Recall** | 0.271 | **0.419** (+0.148) | 0.209 | **0.487** (+0.278) |
| **ROUGE-1 F1** | 0.298 | **0.430** (+0.132) | 0.241 | **0.462** (+0.221) |
| **ROUGE-2 F1** | 0.103 | **0.173** (+0.070) | 0.072 | **0.193** (+0.121) |
| **ROUGE-L F1** | 0.194 | **0.281** (+0.087) | 0.153 | **0.302** (+0.149) |
| **BLEU** | 0.063 | **0.118** (+0.055) | 0.038 | **0.142** (+0.104) |
| **Perfect Scenarios** | 0 / 17 | **6 / 17** | 0 / 17 | **6 / 17** |
| **Trainable Params** | 0 (frozen) | ~1-2% of 7B | 0 (frozen) | ~1-2% of 7B |

> **Key insights:**
> - **Qwen2.5-7B has a stronger zero-shot baseline** (42.0% vs 33.0%) due to its superior base reasoning (MMLU 74.2 vs 62.5)
> - **LoRA fine-tuning delivers massive gains for both models**: +29.5 pp (Qwen) and +35.8 pp (Mistral)
> - **Model 1 (Qwen) achieves higher accuracy (71.5%)** than Model 2 (Mistral + RAG, 68.8%) — a stronger base model matters more than retrieval augmentation
> - **RAG gives Mistral higher ROUGE/keyword scores** than its accuracy alone would suggest — retrieved context improves generation quality even when classification is imperfect

### LLM Benchmark Scores: Implemented Models (7B)

| Model | MMLU | HumanEval | GSM8K | Function Calling |
|-------|------|-----------|-------|------------------|
| **Qwen2.5-7B-Instruct** | 74.2 | 84.1 | 85.4 | 92% |
| Llama-3.1-8B-Instruct | 69.4 | 72.8 | 84.4 | 88% |
| Mistral-7B-Instruct-v0.3 | 62.5 | 40.2 | 52.2 | 85% |

### LLM Benchmark Scores: Planned Models (Frontier)

| Model | MMLU | HumanEval | GSM8K | MT-Bench |
|-------|------|-----------|-------|----------|
| GPT-4o | 88.7 | 90.2 | 95.8 | 9.3 |
| Claude-3.5-Sonnet | 88.7 | 92.0 | 96.4 | 9.2 |
| Gemini-1.5-Pro | 85.9 | 84.1 | 91.0 | 8.8 |

---

## Web-Based System Design & Implementation Demo

### Data-to-Model Pipeline

The end-to-end pipeline from synthetic data generation through multi-agent RCA inference:

```
              +---------------------------+
              |  Python Data Generator    |
              +------------+--------------+
                           |
                           v
              +---------------------------+
              |  Data in JSONL Format     |
              +------------+--------------+
                           |
                           v
              +---------------------------+
              |  Data Transformation      |
              +------------+--------------+
                           |
                           v
              +---------------------------+
              |  Transformed Data         |
              |  Parquet / Evidence        |
              +-----+-------------+-------+
                    |             |
                    v             v
         +--------------+  +--------------+
         |  Agent 1     |  |  Agent 2     |
         |  RCA /       |  |  RCA /       |
         |  Solution    |  |  Solution    |
         |  Generator   |  |  Generator   |
         +---------+----+  +----+---------+
                   |             |
                   v             v
              +---------------------------+
              |  Decision /               |
              |  Reconciliation Agent     |
              +------------+--------------+
                           |
                           v
              +---------------------------+
              |  Validation Agent         |
              +------------+--------------+
                           |
                           v
              +---------------------------+
              |  Final Output             |
              |  Validated RCA +          |
              |  Remediation Plan         |
              +---------------------------+
```

| Pipeline Stage | Description | Output |
|----------------|-------------|--------|
| **Python Data Generator** | Synthesizes K8s failure scenarios across 17 incident types | Raw JSONL incident records |
| **Data Transformation** | Parses, enriches, validates; generates log templates | Structured Parquet files |
| **Agent 1 (Qwen2.5-7B)** | LoRA fine-tuned RCA classification + solution generation | Root cause label + diagnosis |
| **Agent 2 (Mistral-7B)** | LoRA fine-tuned generation with RAG retrieval | Diagnosis + remediation plan |
| **Decision / Reconciliation Agent** | Compares Agent 1 & 2 outputs, resolves disagreements | Consensus RCA + plan |
| **Validation Agent** | Checks structural completeness, hallucination, consistency | Validated final output |

---

### Web Application Architecture

The full-stack system enabling users to submit, analyze, and review K8s incidents through a browser-based interface:

```
                    +---------------------------+
                    |          User             |
                    +------------+--------------+
                                 |
                                 v
                    +---------------------------+
                    |  Frontend - React UI      |
                    |  Dashboard / Incident     |
                    |  Detail / Analyze         |
                    +-----+-------------+-------+
                          |             |
                          v             |
              +---------------------------+     +---------------------------+
              |  Backend API              |<----|  User Feedback            |
              |  Incident Retrieval /     |     +-------------+-------------+
              |  Orchestration / Feedback |                   |
              +-----+-----+--------------+                   v
                    |     |               +---------------------------+
                    |     |               |  Data Layer               |
                    v     |               |  Incident Store /         |
              +---------------------------+  Scenario Payloads /     |
              |  FastAPI Inference        |  Feedback / Training Data|
              |  Service                  +---------------------------+
              +-----+-------------+-------+
                    |             |
                    v             v
         +--------------+  +--------------+
         |  Agent 1     |  |  Agent 2     |
         |  Solution    |  |  Solution    |
         |  Generator   |  |  Generator   |
         +---------+----+  +----+---------+
                   |             |
                   v             v
              +---------------------------+
              |  Decision /               |
              |  Reconciliation Agent     |
              +------------+--------------+
                           |
                           v
              +---------------------------+
              |  Validation Agent         |
              +------------+--------------+
                           |
                           v
              +---------------------------+
              |  Structured RCA Result    |
              |  Diagnosis / Fix Plan /   |
              |  Commands / Verification /|
              |  Rollback                 |
              +---------------------------+
```

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Frontend** | React UI | Dashboard, incident detail views, analyze interface |
| **Backend API** | Python | Incident retrieval, agent orchestration, feedback loop |
| **FastAPI Inference Service** | FastAPI + Transformers | Serves LoRA-adapted models for real-time inference |
| **Agent 1** | Qwen2.5-7B + LoRA | RCA classification + solution generation |
| **Agent 2** | Mistral-7B + LoRA + RAG | RAG-augmented diagnosis + remediation |
| **Decision / Reconciliation** | Consensus logic | Resolves disagreements between Agent 1 & 2 |
| **Validation Agent** | Rule-based + LLM | Structural checks, hallucination detection |
| **Data Layer** | JSON / Parquet / SQLite | Incident store, scenario payloads, feedback, training data |

### ELK Observability Stack on Kubernetes

```
K8s Cluster Events --> Logstash (Ingestion) --> Elasticsearch (Indexing) --> Kibana (Dashboards)
     |                      |                        |                          |
  Pod statuses         Parse & enrich           Indexed storage          Real-time web UI
  Container logs       Filter & route           Full-text search         Incident visualization
  Cluster events       Transform data           Aggregations             Alert monitoring
  Resource metrics     Output routing           Retention policies       Interactive queries
```

| Component | Purpose | Deployment |
|-----------|---------|------------|
| **Kibana** | Real-time log monitoring & incident dashboards | `https://localhost:5601` |
| **Elasticsearch** | Indexed storage and search for K8s signals | K8s StatefulSet |
| **Logstash** | Log ingestion, parsing, and enrichment | K8s Deployment |
| **Kubernetes** | Container orchestration (EKS + Kind) | AWS EKS (2x c7i-flex.large) |
| **Terraform** | Infrastructure as Code provisioning | CI/CD |
| **Flux CD** | GitOps continuous deployment | Automated sync |

### How to Reproduce
1. Open Colab notebook (`train_model1_qwen_colab.ipynb` or `train_model2_mistral_colab.ipynb`)
2. Set runtime to **A100 GPU**
3. Upload parquet file (`agent1_structured.parquet` or `agent2_evidence.parquet`)
4. Run all cells — outputs loss tables, accuracy breakdowns, and downloadable LoRA adapters

---

## Key Takeaways

### ML Model & Evaluation Results
1. **Fine-tuning transforms both models**: Qwen baseline 42.0% → 71.5% (+29.5 pp), Mistral baseline 33.0% → 68.8% (+35.8 pp) — LoRA training is the single biggest driver of performance
2. **Base model quality matters**: Qwen2.5-7B (MMLU 74.2) outperforms Mistral-7B (MMLU 62.5) even when Mistral is augmented with RAG — 71.5% vs 68.8%
3. **RAG compensates for weaker base models**: Mistral's larger relative improvement (+35.8 pp vs +29.5 pp) and higher keyword recall (0.487 vs 0.419) show RAG retrieval partially bridges the base model gap
4. **Model 1 exceeds the 70% target**: Qwen2.5-7B at 71.5% scenario match accuracy with BERTScore F1 of 0.845
5. **Structured outputs are reliable**: Section scores jump from 0.58–0.68 (baselines) to 0.95–0.97 (trained) — fine-tuning teaches consistent Diagnosis / Fix Plan / Verification structure
6. **Semantic quality far exceeds lexical overlap**: BERTScore F1 (0.83–0.85) >> BLEU (0.12–0.14) confirms models produce correct paraphrased diagnoses rather than copying templates

### Comparative Model Results
7. **Dual-agent architecture with reconciliation**: Agent 1 (Qwen) and Agent 2 (Mistral) generate independent RCA solutions, then a Decision/Reconciliation Agent resolves disagreements for higher-confidence output
8. **LoRA is efficient**: ~1% trainable parameters, ~130 MB adapters, single A100 GPU — production-viable for both models

### Web-Based System Design & Implementation
9. **Full-stack web application**: React frontend → Backend API → FastAPI inference service → multi-agent pipeline → validated RCA output with user feedback loop
10. **End-to-end data pipeline**: Python data generator → JSONL → data transformation → Parquet → dual-agent inference → reconciliation → validation → structured RCA result
11. **Full observability pipeline**: ELK stack (Logstash, Elasticsearch, Kibana) on Kubernetes provides real-time log monitoring and incident visualization
12. **Extensible design**: Models 3-4 leverage frontier LLMs (GPT-4o, Claude, Gemini) for blackboard validation and multi-agent debate
