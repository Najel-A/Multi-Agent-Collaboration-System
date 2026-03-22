# End-to-End Data Pipeline

## Multi-Agent Collaboration System — Kubernetes Root Cause Analysis

---

## Layer 1: Data Generation

```
+---------------------------------------------------------------+
|                                                               |
|   k8s_synth_generator_portfolio.py                            |
|                                                               |
|   8,502 balanced Kubernetes incidents                         |
|   18 failure scenarios x 500 samples each                     |
|   3 difficulty levels (easy / medium / hard)                  |
|   498 quality-rejected samples                                |
|                                                               |
|   Each incident contains:                                     |
|     - Cluster context (namespace, workload, image)            |
|     - Fault metadata (scenario_id, variant, fault_params)     |
|     - Observations (kubectl get/describe/events, logs,        |
|       metrics_snapshot)                                       |
|     - Remediation (diagnosis, fix plan, actions,              |
|       verification, rollback)                                 |
|     - Meta (difficulty, noise_level, failure_phase)           |
|                                                               |
|   Output:                                                     |
|     synthetic_source.jsonl  (8,502 records, ~40 MB)           |
|     stats.json              (per-scenario counts)             |
|                                                               |
+---------------------------------------------------------------+
```

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

```
                              |
                              |  transform_incidents.py
                              v
```

---

## Layer 2: Data Transformation & Feature Engineering

```
+---------------------------------------------------------------+
|                                                               |
|   transform_incidents.py                                      |
|                                                               |
|   1. JSON flattening (pd.json_normalize)                      |
|   2. Regex parse kubectl_describe_pod                         |
|      -> pod_status, waiting_reason, error_message,            |
|         restart_count                                         |
|   3. Parse kubectl_get_events table                           |
|      -> event_type, event_reason, event_message               |
|   4. Extract metrics_snapshot                                 |
|      -> restart_count_metrics, oom_killed                     |
|   5. Derive root_cause_family (12 labels)                     |
|   6. Derive symptom_family                                    |
|                                                               |
+-------------------------------+-------------------------------+
|                               |                               |
|   agent1_structured.parquet   |   agent2_evidence.parquet     |
|                               |                               |
|   27 columns, tabular         |   16 columns, text pairs      |
|   Pod status, events,         |   evidence_text +             |
|   restart counts, oom_killed  |   diagnosis_text,             |
|   root_cause_family (12)      |   fix_plan_text,              |
|   symptom_family              |   actions_text,               |
|                               |   verification_text,          |
|                               |   rollback_text               |
|                               |                               |
+-------------------------------+-------------------------------+
```

**Root Cause Family Labels (12):** oom, image_pull, scheduling, missing_secret, configmap, pvc, probe, rbac, dns, connection, quota, other

```
                              |
                              |  80/20 train/test split (seed=42)
                              v
```

---

## Layer 3: Model Training (LoRA Fine-Tuning)

```
+-------------------------------+       +-------------------------------+
|                               |       |                               |
|   Model 1: Mistral-7B LoRA   |       |   Model 2: Qwen2.5-7B LoRA   |
|   model1_mistral_lora.py      |       |   model2_qwen_lora.py         |
|                               |       |                               |
|   Task: RCA Classification    |       |   Task: Diagnosis & Fix Gen   |
|   Input: agent1_structured    |       |   Input: agent2_evidence      |
|          .parquet             |       |          .parquet             |
|                               |       |                               |
|   Base: Mistral-7B-Instruct   |       |   Base: Qwen2.5-7B-Instruct  |
|         -v0.3                 |       |                               |
|   LoRA: r=16, alpha=32,      |       |   LoRA: r=16, alpha=32,      |
|         dropout=0.05          |       |         dropout=0.05          |
|   Quant: 4-bit NF4            |       |   Quant: 4-bit NF4            |
|   Max tokens: 512             |       |   Max tokens: 1024            |
|   Format: Mistral instruct    |       |   Format: ChatML              |
|   Targets: q/k/v/o_proj       |       |   Targets: q/k/v/o_proj       |
|   Training: SFTTrainer,       |       |   Training: SFTTrainer,       |
|     cosine LR (2e-4), AdamW   |       |     cosine LR (2e-4), AdamW   |
|                               |       |                               |
|   Output: Root cause label    |       |   Output: Structured report   |
|     (12 classes)              |       |     Diagnosis, Fix Plan,      |
|                               |       |     Actions, Verification,    |
|                               |       |     Rollback                  |
|                               |       |                               |
|   Artifact:                   |       |   Artifact:                   |
|     LoRA adapter (~130 MB)    |       |     LoRA adapter (~130 MB)    |
|                               |       |                               |
+-------------------------------+       +-------------------------------+
```

**Training Environment:** Google Colab (A100 GPU, 40GB VRAM). Hardware-adaptive: CUDA (4-bit quant), Apple MPS (FP16), CPU fallback.

**Anti-Overfitting:** Early stopping (patience=2), LoRA rank 32, dropout 0.1, weight decay 0.05, label smoothing 0.1 (M1), 80/20 split.

```
                              |
                              |  Adapters loaded into agent pipeline
                              v
```

---

## Layer 4: Multi-Agent Coordination (4 Models, 15 Agents)

Each model inherits all agents from the previous and adds new coordination capabilities.

```
+---------------------------------------------------------------+
|                                                               |
|   M1: Centralized Linear Pipeline (6 agents, ~$0/incident)   |
|                                                               |
|   Data Ingestion --> Preprocessing --> Anomaly Detection      |
|     [message]        [message]       (IF, SVM, LOF)           |
|                                          |                    |
|                                       [message]               |
|                                          v                    |
|                                       Planner                 |
|                                    (Mistral-7B LoRA)          |
|                                          |                    |
|                                       [message]               |
|                                          v                    |
|                                  Executor --> Reviewer         |
|                                                               |
+---------------------------------------------------------------+
                              |
                              |  + Retrieval Agent + Knowledge Base
                              v
+---------------------------------------------------------------+
|                                                               |
|   M2: + RAG & Shared Knowledge (7 agents, ~$0/incident)      |
|                                                               |
|   Inherits M1 pipeline, adds:                                 |
|                                                               |
|   Anomaly Detection --> [NEW] Retrieval Agent                 |
|                               |                               |
|                         [Knowledge Base]                      |
|                          (FAISS / RAG)                        |
|                               |                               |
|                               v                               |
|                     Planner (Qwen2.5-7B LoRA,                 |
|                              RAG-enhanced)                    |
|                               |                               |
|                        Executor --> Reviewer                  |
|                                                               |
+---------------------------------------------------------------+
                              |
                              |  + Intent Classifier + Validation Agent + Blackboard
                              v
+---------------------------------------------------------------+
|                                                               |
|   M3: + Blackboard & Validation (9 agents, ~$0.08-0.15)      |
|                                                               |
|   Inherits M2, adds shared Blackboard (all agents read/write)|
|                                                               |
|   +------- BLACKBOARD (shared state) -------+                |
|   |  intent | retrieved_context |            |                |
|   |  validation_result | rca_plan            |                |
|   +--^-----------^-----------^----------^----+                |
|      |           |           |          |                     |
|   [NEW]      Retrieval    [NEW]      Planner                 |
|   Intent     Agent (M2)  Validation  (Opus /                 |
|   Classifier             Agent       GPT-4o)                 |
|   (GPT-mini/             (GPT-4o/                            |
|    Haiku)                 Sonnet)                             |
|                                                               |
+---------------------------------------------------------------+
                              |
                              |  + 3 Debaters + Referee + Safety + Memory
                              v
+---------------------------------------------------------------+
|                                                               |
|   M4: + Debate, Safety & Memory (14 agents, ~$0.30-0.60)     |
|                                                               |
|   Inherits M3. Blackboard output feeds debate framework:      |
|                                                               |
|       [Model 3 Blackboard Output]                             |
|               |           |           |                       |
|               v           v           v                       |
|          Debater 1   Debater 2   Debater 3                    |
|          (Opus)      (GPT-4o)    (DeepSeek-R1)               |
|               |           |           |                       |
|               +--- Cross-Examination --+                      |
|                           |                                   |
|                           v                                   |
|                       Referee                                 |
|              (evidence-based scoring:                         |
|               35% grounding, 25% logic,                       |
|               20% specificity, 20% resilience)                |
|                           |                                   |
|                           v                                   |
|                    Safety Agent (HiTL)                         |
|              (low=auto, med=30s delay,                        |
|               high=human approval)                            |
|                           |                                   |
|                           v                                   |
|                       Executor                                |
|                           |                                   |
|                           v                                   |
|               Context Memory Manager                          |
|              (indexes outcomes for                            |
|               self-improvement via RAG)                        |
|                                                               |
+---------------------------------------------------------------+
```

**Agent Inheritance Summary:**

| Model | Total | New Agents | Coordination |
|-------|-------|------------|-------------|
| M1 | 6 | Data Ingestion, Preprocessing, Anomaly Detection, Planner, Executor, Reviewer | Sequential messages |
| M2 | 7 | + Retrieval Agent | + Shared memory (RAG) |
| M3 | 9 | + Intent Classifier, Validation Agent | + Blackboard + validation chain |
| M4 | 14 | + Debater 1, 2, 3, Referee, Safety Agent, Memory Manager | + Debate + HiTL + self-improvement |

```
                              |
                              |  RCA result + remediation plan
                              v
```

---

## Layer 5: Execution & Infrastructure

```
+-------------------------------+       +-------------------------------+
|                               |       |                               |
|   Automated Remediation       |       |   ELK Observability Stack     |
|                               |       |                               |
|   Executor Agent:             |       |   Filebeat (DaemonSet)        |
|     - kubectl commands        |       |     |                         |
|     - Config changes          |       |     v                         |
|     - Restarts                |       |   Logstash 8.14               |
|                               |       |     (parse & normalize)       |
|   Reviewer Agent:             |       |     |                         |
|     - Post-fix verification   |       |     v                         |
|     - Rollback if failed      |       |   Elasticsearch 8.14          |
|                               |       |     (index & search)          |
|   Autonomy Tiers:             |       |     |                         |
|     Tier 1: read-only (auto)  |       |     v                         |
|     Tier 2: low/med risk      |       |   Kibana 8.14                 |
|       (30s delay for override)|       |     (dashboards & alerts)     |
|     Tier 3: high risk         |       |                               |
|       (human approval req'd)  |       |   3 Environments:             |
|                               |       |     Local (Kind)              |
|   Never-autonomous:           |       |     AWS Staging (Flux CD)     |
|     - Delete pods/PVCs        |       |     AWS Prod (Terraform,      |
|     - Modify RBAC             |       |       EKS v1.31,              |
|     - Scale to 0              |       |       2x c7i-flex.large)     |
|     - Confidence < 50%        |       |                               |
|                               |       |                               |
+-------------------------------+       +-------------------------------+
```

```
                              |
                              |  Outcomes feed back to Memory Manager (M4)
                              v
```

---

## Layer 6: Evaluation & Outputs

```
+---------------------------------------------------------------+
|                                                               |
|   Cross-Model Evaluation                                      |
|   Same 500 held-out incidents for all 4 models                |
|                                                               |
|   +-------------+  +-------------+  +-----------+  +---------+
|   | M1           |  | M2           |  | M3         |  | M4     |
|   | Fuzzy acc    |  | Structure    |  | Trajectory |  | Debate |
|   | Confusion    |  |   score 40%  |  | coherence  |  | diver- |
|   |   matrix     |  | Keyword      |  | Hallucin.  |  |  sity  |
|   | Per-class    |  |   overlap    |  |   detect.  |  | Error- |
|   |   P/R/F1     |  |          60% |  |   rate     |  | repeat |
|   | Difficulty   |  | = Combined   |  | Tool-call  |  | Safety |
|   |   breakdown  |  |   score      |  |   accuracy |  |  viol. |
|   |              |  |              |  |            |  | Human  |
|   | Target:      |  | Target:      |  | Target:    |  | over-  |
|   |  >= 70%      |  |  >= 70%      |  |  RCA > 75% |  |  ride  |
|   +-------------+  +-------------+  +-----------+  +---------+
|                                                               |
|   Statistical Tests:                                          |
|     Friedman test (4 models)                                  |
|     McNemar's test (pairwise: M2 vs M3, M3 vs M4)            |
|     Accuracy vs. cost efficiency curve                        |
|                                                               |
+---------------------------------------------------------------+

+---------------------------------------------------------------+
|                                                               |
|   Final Outputs                                               |
|                                                               |
|   +-------------+  +-------------+  +-----------+  +---------+
|   | RCA Report   |  | Verification|  | Rollback   |  | Kibana |
|   |              |  | Steps       |  | Plan       |  | Dash-  |
|   | Root cause   |  |             |  |            |  | boards |
|   | label +      |  | Post-fix    |  | Revert     |  |        |
|   | diagnosis +  |  | kubectl     |  | instruc-   |  | Live   |
|   | fix plan +   |  | commands    |  | tions if   |  | logs,  |
|   | actions      |  | to confirm  |  | remediation|  | alerts,|
|   |              |  | resolution  |  | fails      |  | agent  |
|   |              |  |             |  |            |  | activ- |
|   |              |  |             |  |            |  | ity    |
|   +-------------+  +-------------+  +-----------+  +---------+
|                                                               |
+---------------------------------------------------------------+
```

---

## Summary — Complete Data Journey

```
+------------+     +-------------+     +-----------+     +--------+     +-----------+     +---------+
| Layer 1    |     | Layer 2     |     | Layer 3   |     | Layer 4|     | Layer 5   |     | Layer 6 |
|            |     |             |     |           |     |        |     |           |     |         |
| K8s        | --> | Transform   | --> | LoRA      | --> | Agent  | --> | Execute   | --> | Evaluate|
| Incidents  |     | to Parquet  |     | Train     |     | Coord. |     | + Observe |     | + Output|
|            |     |             |     |           |     |        |     |           |     |         |
| 8,502      |     | 2 agent-    |     | Mistral + |     | 4 model|     | Remediate |     | Cross-  |
| synthetic  |     | specific    |     | Qwen      |     | 14 agnt|     | + ELK     |     | model   |
| JSONL      |     | Parquet     |     | adapters  |     | M1-M4  |     | stack     |     | compare |
+------------+     +-------------+     +-----------+     +--------+     +-----------+     +---------+
```
