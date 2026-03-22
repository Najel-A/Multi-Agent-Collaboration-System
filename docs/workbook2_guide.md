# DATA 298B Workbook 2 - Section Guide

## Multi-Agent Collaboration System for Kubernetes Root Cause Analysis

This document maps each section of the DATA 298B Workbook 2 template to the relevant components of our Multi-Agent Collaboration System repository, explaining what to include and where to find the information.

---

## 4. Model Development

### 4.1 Model Proposals

**What to cover:** Specify the applied, deployed, improved, proposed, and/or ensembled models to each of the targeted problems in terms of concepts, inputs/outputs, features, model architectures, algorithms, etc.

**From our repo:**

We propose 5 progressively sophisticated multi-agent model architectures targeting Kubernetes Root Cause Analysis (RCA):

| Model | Architecture | Input | Output | Key Algorithms |
|-------|-------------|-------|--------|----------------|
| **Model 1** | Statistical & Classical ML MAS | Structured features (27 columns from `agent1_structured.parquet`) — pod status, restart count, error messages, event signals | Root cause family classification (e.g., "oom", "rbac", "image_pull") | Isolation Forest, One-Class SVM, LOF + Mistral-7B LoRA |
| **Model 2** | Deep Sequence & Transformer MAS | Raw text evidence (`agent2_evidence.parquet`) — kubectl output, logs, metrics | Diagnosis text, fix plan, verification steps, rollback plan | LSTM/GRU, LogBERT, RAG w/ FAISS + Qwen-7B LoRA |
| **Model 3** | SMART Knowledge-Intensive MAS | User intent + retrieval context | Validated, grounded RCA responses | Intent Classifier, Retrieval-Augmented Validation, Claude Opus 4 + GPT-4o |
| **Model 4** | Decentralized Bidding MAS (Contract Net) | Incident data broadcast to specialist agents | Coordinated specialist analysis via auction | RL-tuned bidding policies, specialist agents (Security, Performance, Network, DB) |
| **Model 5** | Self-Evolving Cognitive Hybrid MAS | Multi-model debate inputs | Consensus diagnosis via evidence scoring | Heterogeneous debaters (Opus, GPT-4o, DeepSeek-R1, Llama-70B), Referee agent |

**Key files:**
- `docs/multi_agent_models.md` — Full architecture specs for all 5 models and agent types
- `agents/models/model1_mistral_lora.py` — Mistral-7B LoRA (classification)
- `agents/models/model2_qwen_lora.py` — Qwen-7B LoRA (generation)
- `docs/agents_creation_outline_2026-03-18.md` — Agent definitions and roles

**Ensemble approach:** Models 1 and 2 serve as the baseline ensemble — Model 1 classifies the root cause family from structured signals, and Model 2 generates the human-readable diagnosis and remediation plan from text evidence. Models 3-5 build on this foundation with increasing sophistication.

---

### 4.2 Model Supports

**What to cover:** Describe the platform, framework, environment, and technologies supporting the development and execution of each model; provide diagrams of architecture, components, data flows, etc.

**From our repo:**

**Training Platform:**
- Google Colab (A100 GPU) for fine-tuning — see `python_notebooks/train_model1_mistral_colab.ipynb` and `python_notebooks/train_model2_qwen_colab.ipynb`
- Local training support for Apple MPS and CPU fallback (in `model1_mistral_lora.py`, `model2_qwen_lora.py`)

**Frameworks & Libraries:**
- **Hugging Face Transformers** — base model loading, tokenization
- **PEFT (Parameter-Efficient Fine-Tuning)** — LoRA adapter injection
- **TRL (Transformer Reinforcement Learning)** — `SFTTrainer` for supervised fine-tuning
- **BitsAndBytes** — 4-bit quantization (`BitsAndBytesConfig`)
- **Flash Attention 2** — optimized attention on A100 GPUs
- **PyArrow / Pandas** — Parquet data handling
- **Apache Spark** — log enrichment (`spark_data.ipynb`)

**Infrastructure (3 deployment tiers):**

| Tier | Technology | Path |
|------|-----------|------|
| Local Dev | Kind cluster + ELK stack | `clusters/elk/` |
| AWS Staging | EKS + Flux CD GitOps | `clusters/elk-aws/` |
| AWS Production | EKS + Terraform IaC | `clusters/elk-prod/infrastructure.tf` |

**Observability Stack (ELK):**
- Elasticsearch — indexing and search
- Logstash — log processing/enrichment
- Kibana — visualization dashboards
- Filebeat — log collection/shipping
- ECK (Elastic Cloud on Kubernetes) — Kubernetes-native deployment

**Data Flow:**
```
k8s_synth_generator_portfolio.py
  --> synthetic_source.jsonl (8,502 incidents, 18 scenarios)
    --> transform_incidents.py
      --> agent1_structured.parquet (27 features, tabular)
      --> agent2_evidence.parquet (text evidence)
        --> Model 1 (Mistral LoRA) --> RCA Classification
        --> Model 2 (Qwen LoRA)   --> Diagnosis + Remediation
```

**Key files:**
- `docs/architecture_2026-03-20.md` — End-to-end system architecture diagram
- `docs/data_pipeline_2026-03-18.md` — Data generation and transformation flow
- `clusters/elk-prod/infrastructure.tf` — Terraform production infrastructure
- `clusters/elk/Makefile` — Local cluster setup

---

### 4.3 Model Comparison and Justification

**What to cover:** For each targeted problem, compare the final selected and deployed models regarding strengths, targeted problems, approaches, data types, limitations; provide justification for each model.

**From our repo:**

**Problem: RCA Classification (Model 1 — Mistral-7B LoRA)**

| Aspect | Details |
|--------|---------|
| **Why Mistral-7B** | Strong instruction-following at 7B scale; efficient for classification tasks; open-source with permissive license |
| **Why LoRA** | Full fine-tuning of 7B params is prohibitive; LoRA (rank 16) trains only ~0.1% of parameters while achieving competitive accuracy |
| **Why 4-bit quantization** | Enables training on single A100 (40GB) or even consumer GPUs; minimal accuracy loss |
| **Strengths** | Fast inference, low memory footprint, structured input handling |
| **Limitations** | Limited to predefined root cause families; may struggle with novel failure modes not in training data |

**Problem: Diagnosis Generation (Model 2 — Qwen-7B LoRA)**

| Aspect | Details |
|--------|---------|
| **Why Qwen-7B** | Excellent multilingual and code understanding; strong at structured text generation; ChatML native format |
| **Why broader LoRA targets** | Includes MLP layers (`gate_proj`, `up_proj`, `down_proj`) in addition to attention — critical for generation quality |
| **Why longer context (1024 vs 512)** | Diagnosis generation requires richer output (diagnosis + fix plan + verification + rollback) |
| **Strengths** | Produces actionable, structured remediation plans; handles complex kubectl output |
| **Limitations** | Generation quality depends on evidence quality; may hallucinate steps for rare scenarios |

**Why 5 progressive models:**
- Model 1-2 (Baseline): Prove value over manual ops with minimal cost
- Model 3 (SMART): Add tool-augmented grounding to reduce hallucination
- Model 4 (Bidding): Enable scalable specialist coordination
- Model 5 (Debate): Maximize reasoning quality via multi-model consensus

**Key files:**
- `docs/evaluation-metrics.md` — LLM benchmark comparisons (MMLU, HumanEval, etc.)
- `docs/multi_agent_models.md` — Side-by-side architecture comparison

---

### 4.4 Model Evaluation Methods

**What to cover:** Present evaluation methods and metrics for each model, e.g., accuracy, loss, ROC/AOC, MSRE, etc. Specify the evaluation methods and metrics for each target problem and solution.

**From our repo:**

**Model 1 (Classification) Metrics:**
- **Exact Accuracy** — case-insensitive exact match of predicted vs. true root cause label
- **Fuzzy Accuracy** — substring containment for partial matches (primary metric)
- **Per-class Breakdown** — accuracy per root cause family (oom, rbac, image_pull, etc.)
- **Target**: >= 70% fuzzy accuracy on held-out test set

**Model 2 (Generation) Metrics:**
- **Structure Score (40% weight)** — presence of required sections (Diagnosis, Fix Plan, Verification)
- **Keyword Overlap (60% weight)** — fraction of ground truth keywords found in prediction
- **Combined Score** >= 70%

**System-Level Metrics:**

| Metric | Target | Category |
|--------|--------|----------|
| F1-Score | > 0.85 | Detection |
| Precision / Recall | > 0.85 | Detection |
| AUC-ROC | > 0.90 | Detection |
| PA-F1 (time-series) | > 0.85 | Detection |
| MTTD (Mean Time to Detect) | < 5 min | Operational |
| MTTR (Mean Time to Remediate) | < 30 min | Operational |
| RCA Accuracy | > 75% | Operational |
| Throughput | > 10K logs/sec | Performance |
| Latency (p95) | < 5 sec | Performance |

**Training Diagnostics:**
- Training loss vs. validation loss curves (early stopping with patience=2)
- Overfitting detection via train/val divergence

**Key files:**
- `docs/evaluation-metrics.md` — Complete evaluation framework
- `docs/model_training_updates.md` — Anti-overfitting refinements and training curves

---

### 4.5 Model Validation and Evaluation Results

**What to cover:** Present and compare detailed ML results based on selected model evaluation methods; present the solution to each targeted problem in terms of validated results, including accuracy, loss, etc. Include original images/data, result images/data, and validated images/data.

**From our repo:**

**Anti-Overfitting Refinements (v2 -> v3):**

| Technique | Old (v2) | New (v3) | Impact |
|-----------|----------|----------|--------|
| Early stopping | None | patience=2 | Stops when val loss plateaus |
| LoRA rank | 64 | 32 | Reduces memorization capacity |
| LoRA dropout | 0.05 | 0.1 | Forces redundant representations |
| Weight decay | 0.01 | 0.05 | Stronger L2 penalty |
| Label smoothing | None | 0.1 (Model 1) | Softens hard targets |
| Train/val split | 85/15 | 80/20 | Larger validation set |
| Epochs | 8-10 | 7 (max) | Lower ceiling + early stopping |

**Where to find results:**
- Training notebooks contain loss curves and evaluation outputs: `python_notebooks/train_model1_mistral_colab.ipynb`, `python_notebooks/train_model2_qwen_colab.ipynb`
- Trained adapter checkpoints: `agents/models/trained/`
- `docs/model_training_updates.md` — Documents the overfitting issue and resolution

**What to include in the final report:**
- Training/validation loss curves (from Colab notebook outputs)
- Per-class accuracy tables for Model 1
- Sample generated diagnoses vs. ground truth for Model 2
- Confusion matrix for root cause classification
- Before/after comparison of anti-overfitting changes

---

## 5. Data Analytics and Intelligent System

### 5.1 System Requirements Analysis

**What to cover:** Describe system boundary, actors and use cases; describe high-level data analytics and machine learning functions and capabilities.

**From our repo:**

**System Boundary:** The Multi-Agent Collaboration System operates within a Kubernetes cluster environment, ingesting logs, metrics, and events to perform automated root cause analysis and remediation.

**Actors:**
- **SRE / DevOps Engineers** — primary users who receive RCA reports and remediation recommendations
- **Kubernetes Clusters** — source of incident data (logs, events, metrics)
- **CI/CD Pipelines** — trigger automated remediation actions
- **ELK Stack** — observability layer for log aggregation and visualization

**Use Cases:**
1. Detect anomalous pod behavior (CrashLoopBackOff, OOM, image pull errors, etc.)
2. Classify root cause from structured signals (18 failure scenarios)
3. Generate actionable diagnosis and remediation plans
4. Visualize incident trends and RCA results via Kibana dashboards
**ML Functions:**
- Anomaly detection (Isolation Forest, One-Class SVM, LOF)
- Root cause classification (Mistral-7B LoRA)
- Diagnosis generation (Qwen-7B LoRA)
- Retrieval-augmented generation for historical incident matching

**Key files:**
- `docs/architecture_2026-03-20.md` — System architecture and boundaries
- `docs/agents_creation_outline_2026-03-18.md` — Agent roles and use case mapping

---

### 5.2 System Design

**What to cover:** Present system architecture and infrastructure with AI-powered function components, system user groups, system inputs/outputs, and connectivity; present system data management and data repository design; present system user interface design, terms of system mockup diagram and dashboard UI templates.

**From our repo:**

**Architecture Overview:**
```
[K8s Cluster] --> [Filebeat] --> [Logstash] --> [Elasticsearch]
                                                      |
                                                  [Kibana] (Dashboard UI)
                                                      |
[Synthetic Data Generator] --> [Data Pipeline] --> [Agent 1: RCA Classifier]
                                                --> [Agent 2: Diagnosis Generator]
                                                --> [Agents 3-5: Advanced MAS]
```

**Infrastructure Components:**
- **VPC**: 10.0.0.0/16 with 2 public subnets across 2 AZs
- **EKS Cluster**: v1.31, 2x c7i-flex.large worker nodes
- **GitOps**: Flux CD for continuous deployment
- **IaC**: Terraform for production provisioning

**Data Repository Design:**
- `data/synthetic_source.jsonl` — raw synthetic incidents (8,502 records)
- `data/processed/agent1_structured.parquet` — 27-column structured features
- `data/processed/agent2_evidence.parquet` — text evidence for LLM consumption
- `agents/models/trained/` — LoRA adapter checkpoints

**UI/Dashboard:**
- Kibana dashboards for real-time log visualization and incident monitoring
- ELK stack provides the primary user interface for observability

**Key files:**
- `docs/architecture_2026-03-20.md` — Full system design with diagrams
- `clusters/elk-prod/infrastructure.tf` — Terraform infrastructure definition
- `clusters/elk/` — ELK stack Kubernetes manifests

---

### 5.3 Intelligent Solution

**What to cover:** Present developed AI and ML solutions for each targeted problem, including integrated solutions, ensembled, developed and applied ML models; describe required project input datasets, expected outputs, supporting system contexts, and solution APIs.

**From our repo:**

**Solution 1: RCA Classification (Model 1)**
- **Input**: Structured incident features from `agent1_structured.parquet` (namespace, pod_status, waiting_reason, error_message, restart_count, event signals — 27 columns total)
- **Model**: Mistral-7B with LoRA (rank=16, alpha=32) + 4-bit quantization
- **Output**: Root cause family label (e.g., "oom", "rbac", "dns", "image_pull", "probe_failure")
- **Implementation**: `agents/models/model1_mistral_lora.py`

**Solution 2: Diagnosis & Remediation Generation (Model 2)**
- **Input**: Evidence text from `agent2_evidence.parquet` (kubectl describe output, log excerpts, metric summaries)
- **Model**: Qwen-7B with LoRA (rank=16, alpha=32, targets attention + MLP) + 4-bit quantization
- **Output**: Structured markdown with Diagnosis, Fix Plan, Verification Steps, Rollback Plan
- **Implementation**: `agents/models/model2_qwen_lora.py`

**Integrated Pipeline:**
```
Incident Data --> transform_incidents.py --> Agent 1 (classify) --> Agent 2 (diagnose)
                                                                        |
                                                              Remediation Plan --> SRE
```

**28 Agent Types** span data ingestion, preprocessing, anomaly detection, planning, coordination, analysis, debate, execution, safety, and memory — detailed in `docs/multi_agent_models.md`.

---

### 5.4 System Supporting Environment

**What to cover:** Present the information and features of system supporting environment, including technologies, platforms, frameworks, etc.

**From our repo:**

| Category | Technology | Purpose |
|----------|-----------|---------|
| **Language** | Python 3.10+ | Primary development language |
| **ML Framework** | PyTorch + Hugging Face Transformers | Model training and inference |
| **Fine-Tuning** | PEFT (LoRA), TRL (SFTTrainer) | Parameter-efficient adaptation |
| **Quantization** | BitsAndBytes (4-bit NF4) | Memory-efficient training |
| **Attention** | Flash Attention 2 | Optimized GPU training on A100 |
| **Data Processing** | Pandas, PyArrow, Apache Spark | Data pipeline and transformation |
| **Container Orchestration** | Kubernetes (Kind, EKS) | Deployment platform |
| **Infrastructure as Code** | Terraform | Production cluster provisioning |
| **GitOps** | Flux CD | Continuous deployment |
| **Observability** | ELK Stack (Elasticsearch, Logstash, Kibana, Filebeat) | Log aggregation and visualization |
| **Cloud Provider** | AWS (EKS, VPC, IAM) | Production hosting |
| **Training Platform** | Google Colab (A100 GPU) | Model fine-tuning |
| **Version Control** | Git / GitHub | Source code management |
| **Notebooks** | Jupyter / Colab | Interactive development |

---

## 6. System Evaluation and Visualization

### 6.1 Analysis of Model Execution and Evaluation Results

**What to cover:** Evaluate the model output with tagged/labelled targets; describe the methodology of measuring accuracy/loss, precision/recall/F-score, or AUC, confusion metrics, etc.

**From our repo:**

**Model 1 Evaluation Methodology:**
- Exact match accuracy: `predicted.lower().strip() == ground_truth.lower().strip()`
- Fuzzy accuracy: `ground_truth in predicted` (substring containment)
- Per-class breakdown across 18 root cause categories
- Confusion matrix of predicted vs. actual root cause families

**Model 2 Evaluation Methodology:**
- Structure score (40%): binary check for presence of "Diagnosis", "Fix Plan", "Verification" headings
- Keyword overlap (60%): `|keywords_predicted ∩ keywords_truth| / |keywords_truth|`
- Combined weighted score

**System-Level:**
- F1-Score, Precision, Recall for detection (target > 0.85)
- AUC-ROC for binary anomaly detection (target > 0.90)
- MTTD and MTTR for operational efficiency

**Key file:** `docs/evaluation-metrics.md`

---

### 6.2 Achievements and Constraints

**What to cover:** Describe the achievements of solving the target problem(s) and the constraints encountered.

**From our repo:**

**Achievements:**
- Complete synthetic data pipeline generating 8,502 realistic K8s incidents across 18 failure scenarios
- Two fine-tuned LLM agents (Mistral-7B + Qwen-7B) with LoRA for classification and generation
- Three-tier infrastructure (local, staging, production) with full IaC and GitOps
- Anti-overfitting refinements resolving train/val loss divergence
- Comprehensive architecture for 5 progressive model levels with agent types
- ELK observability stack for real-time monitoring

**Constraints:**
- Synthetic data only — real production K8s incidents not yet incorporated
- Models 3-5 architecturally designed but not yet implemented
- Training limited to Google Colab session time constraints
- 7B parameter models may lack reasoning depth for complex multi-step RCA (addressed by Models 3-5)

---

### 6.3 System Quality Evaluation of Model Functions and Performance

**What to cover:** Evaluate the correctness of the model and the run-time performance of meeting system response time targets.

**From our repo:**

**Correctness Targets:**
- Model 1 fuzzy accuracy: >= 70%
- Model 2 combined score: >= 70%
- System RCA accuracy: > 75%

**Performance Targets:**

| Metric | Target | How Measured |
|--------|--------|-------------|
| Detection latency (p95) | < 5 sec | End-to-end from log ingestion to classification |
| Throughput | > 10K logs/sec | ELK pipeline capacity |
| MTTD | < 5 min | Time from incident start to alert |
| MTTR | < 30 min | Time from detection to remediation |
| Inference (Model 1) | < 2 sec | Single classification on GPU |
| Inference (Model 2) | < 10 sec | Full diagnosis generation on GPU |

**Key file:** `docs/evaluation-metrics.md`

---

### 6.4 System Visualization

**What to cover:** Apply visualization methodologies to present project data, analysis results, and ML outcomes, e.g., data analytics outcomes and map-based UI with different classification results.

**From our repo:**

**Visualization Components:**
- **Kibana Dashboards** — real-time log monitoring, incident timelines, anomaly heatmaps
- **Training Curves** — loss plots from Colab notebooks showing training vs. validation loss
- **Confusion Matrices** — classification results from Model 1 evaluation
- **Per-Class Accuracy Charts** — breakdown across 18 root cause categories
- **Architecture Diagrams** — system flow from `docs/architecture_2026-03-20.md`
- **Data Distribution Plots** — incident distribution across scenarios from synthetic data generation

**Key files for visualization data:**
- `python_notebooks/train_model1_mistral_colab.ipynb` — training plots
- `python_notebooks/train_model2_qwen_colab.ipynb` — training plots
- `rca_data_transforms.ipynb` — data transformation visualizations
- `clusters/elk/` — Kibana dashboard configurations

---

## Appendices

### Appendix A - System Testing

**What to cover:** Present the test results of required use cases in terms of a sequence of GUI screens for each required use case.

**From our repo:**
- Screenshots of Kibana dashboards showing incident detection
- ELK stack log ingestion verification
- Model inference examples (input prompt -> classification output for Model 1, input evidence -> diagnosis output for Model 2)
- Kubernetes cluster status verification (`kubectl get nodes`, `kubectl get pods`)
- Infrastructure provisioning screenshots (`terraform apply` output)

---

### Appendix B - Project Data Source and Management Store

**What to cover:** Provide project data source information, e.g., training data, test data, etc.

**From our repo:**

| Dataset | Path | Format | Records | Description |
|---------|------|--------|---------|-------------|
| Raw Synthetic Data | `data/synthetic_source.jsonl` | JSONL | 8,502 | 18 K8s failure scenarios, 500 each |
| Agent 1 Structured | `data/processed/agent1_structured.parquet` | Parquet | 8,502 | 27 columns of structured features |
| Agent 2 Evidence | `data/processed/agent2_evidence.parquet` | Parquet | 8,502 | Text evidence + diagnosis + fix plan |
| Trained Adapters | `agents/models/trained/` | Safetensors | — | LoRA checkpoints for Mistral + Qwen |

**Data Generation:** `k8s_synth_generator_portfolio.py` — configurable per-scenario count, quality filtering (498 rejected)

**Train/Val Split:** 80/20 (updated from 85/15 for better validation signal)

---

### Appendix C - Project Program Source Library, Presentation, and Demonstration

**What to cover:** Upload project program artifacts, program source codes, PPTs, and demo videos.

**From our repo:**

| Artifact | Location |
|----------|----------|
| Source Code | GitHub repository (this repo) |
| Training Notebooks | `python_notebooks/` |
| Data Pipeline Scripts | `k8s_synth_generator_portfolio.py`, `transform_incidents.py` |
| Model Implementations | `agents/models/model1_mistral_lora.py`, `agents/models/model2_qwen_lora.py` |
| Infrastructure Code | `clusters/elk-prod/infrastructure.tf` |
| Documentation | `docs/` directory |
| Report | `Workbook2_Report.docx` |

**Sub-directories to set up in Google Drive:**
- Submitted Documents
- PPTs
- Demo Videos
- Program Sources
