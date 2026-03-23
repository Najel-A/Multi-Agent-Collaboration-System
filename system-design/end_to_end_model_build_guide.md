# End-to-End Build Guide for All 4 Models

## Shared Foundation: Data Pipeline (All Models Depend on This)

### Step 1 — Synthetic Data Generation

**Script:** `k8s_synth_generator_portfolio.py` (2,238 lines)

**What it produces:** 8,502 synthetic Kubernetes incidents across 18 failure scenarios (OOM kill, image pull failure, DNS errors, RBAC forbidden, missing secrets, etc.), balanced at ~500 samples per scenario.

**Why synthetic?** Real K8s incidents are sensitive, inconsistent, and unlabeled. Synthetic data gives balanced, fully-labeled, reproducible ground truth. Each incident contains:
- **Context:** cluster_id, namespace, workload_name, container, image
- **Fault:** scenario_id (ground truth), variant, fault_params
- **Observations:** `kubectl describe pod`, `kubectl get events`, container logs, metrics_snapshot
- **Remediation:** diagnosis, fix_plan, actions_structured, verification, rollback
- **Meta:** difficulty (easy/medium/hard), noise_level, failure_phase

```bash
python k8s_synth_generator_portfolio.py --per_scenario 500 --outdir ./data --seed 7
# -> data/synthetic_source.jsonl (8,502 rows)
# -> data/stats.json
```

### Step 2 — Data Transformation

**Script:** `transform_incidents.py` (528 lines)

Splits the single JSONL into **two agent-specific Parquet datasets** because each agent type needs data in a different shape:

```
data/synthetic_source.jsonl
    |
    +---> data/processed/agent1_structured.parquet  (27 columns, tabular)
    |    Used by: Model 1 (classification)
    |
    +---> data/processed/agent2_evidence.parquet    (16 columns, text pairs)
         Used by: Model 2 (generation + RAG)
```

**Agent 1 transformations** (for the classification agent):
1. Flatten nested JSON -> dot-separated columns
2. Regex parse `kubectl_describe_pod` -> extract `pod_status`, `waiting_reason`, `error_message`, `restart_count`
3. Parse `kubectl_get_events` table -> extract `event_type`, `event_reason`, `event_message`
4. Extract `metrics_snapshot` dict -> `restart_count_metrics`, `oom_killed`
5. Derive `root_cause_family`: map 18 fine-grained scenario_ids -> 12 coarse labels (oom, image_pull, scheduling, dns, rbac, probe, connection, configmap, missing_secret, pvc, quota, other)
6. Derive `symptom_family`: map pod_status/waiting_reason -> observable symptoms (CrashLoopBackOff, ImagePullBackOff, etc.)

**Agent 2 transformations** (for the generation agent):
1. Concatenate all kubectl outputs, logs, and metrics into a single `evidence_text` string
2. Extract remediation fields as separate text columns: `diagnosis_text`, `fix_plan_text`, `actions_text`, `verification_text`, `rollback_text`

```bash
python transform_incidents.py --input data/synthetic_source.jsonl --outdir data/processed
```

---

## Model 1: Centralized Linear Pipeline (Baseline)

### Purpose
Establish a minimum viable multi-agent system. Answer: *"What's the simplest agent pipeline that beats pure rules?"* This is the control group everything else must beat.

### Architecture: Sequential Message Passing (6 Agents)

```
K8s Pod Fails
    |
    v
[Data Ingestion Agent]  --message-->  [Preprocessing Agent]  --message-->  [Anomaly Detection Agent]
 Collects logs from ELK               Parses, normalizes,                  Scores with Isolation Forest,
                                       validates fields                     One-Class SVM, LOF
    |
    v
[Planner Agent]  --message-->  [Executor Agent]  --message-->  [Reviewer Agent]
 Mistral-7B LoRA               Runs kubectl                    Verifies fix,
 classifies root cause          remediation cmds                triggers rollback
```

Each agent passes a structured message to the next. No shared state, no parallelism -- pure sequential pipeline.

### Agent Breakdown

| # | Agent | Type | ML Method | Purpose |
|---|-------|------|-----------|---------|
| 1 | Data Ingestion | Deterministic | None | Polls Elasticsearch for new logs, normalizes formats |
| 2 | Preprocessing | Deterministic | Regex parsing | Extracts structured fields from raw kubectl text |
| 3 | Anomaly Detection | Statistical ML | Isolation Forest, One-Class SVM, LOF | Flags statistical outliers in numeric features (error counts, restart rates) |
| 4 | **Planner** | **LLM (fine-tuned)** | **LoRA SFT** | **Classifies root cause from structured signals** |
| 5 | Executor | Deterministic | None | Runs remediation actions (kubectl patch, restart, scale) |
| 6 | Reviewer | Deterministic | None | Post-fix verification, rollback trigger |

### The Planner Agent -- How Model 1 Is Built

**File:** `agents/models/model1_mistral_lora.py` (325 lines)

#### Why Mistral-7B-Instruct-v0.3?
- **7B parameters** -- small enough to fine-tune on a single A100 GPU with 4-bit quantization
- **Instruction-tuned** -- already understands system/user/assistant format; classification is a natural prompt-completion task
- **Open-source, self-hosted** -- $0/inference, no API dependency, suitable for a baseline
- **Mistral-7B benchmarks:** MMLU 62.5, HumanEval 40.2, GSM8K 52.2 -- adequate for structured classification (not reasoning-heavy), and the LoRA fine-tuning compensates for lower base capability on our specific domain

#### Data Flow Through Training

```
agent1_structured.parquet (8,502 rows, 27 columns)
    |
    v  format_incident_prompt(): pick 13 key fields
    |
    v  build_chat_text(): wrap in Mistral instruct format
    |
    v  Format:
    |   <s>[INST] {SYSTEM_PROMPT}
    |   Classify the root cause of this Kubernetes incident:
    |
    |   Namespace: backend-prod
    |   Pod Status: CrashLoopBackOff
    |   Waiting Reason: OOMKilled
    |   Error Message: Exit code 137
    |   Event Type: Warning
    |   Event Reason: BackOff
    |   Event Message: Container exited with code 137
    |   Restart Count: 14
    |   OOM Killed: True
    |   Symptom Family: CrashLoopBackOff
    |   Difficulty: medium
    |   Noise Level: 0.1
    |   [/INST] oom</s>
    |
    v  train_test_split(test_size=0.2, seed=42)
    |
    +---> 6,801 train examples
    +---> 1,701 test examples
```

#### Training Configuration & Reasoning

| Setting | Value | Why |
|---------|-------|-----|
| **LoRA rank (r)** | 16 | Rank 16 provides sufficient expressiveness for 12-class classification without overfitting on 6.8K samples. Higher ranks waste parameters; lower ranks underfit. |
| **LoRA alpha** | 32 | Alpha/r = 2.0 scaling factor. Standard practice: alpha = 2x r gives effective learning rate matching full fine-tuning. |
| **LoRA target modules** | `q_proj, k_proj, v_proj, o_proj` | Attention layers only. For classification, attention heads are the primary mechanism for learning "which input fields matter for which labels." |
| **LoRA dropout** | 0.05 | Light regularization. Prevents co-adaptation of adapter weights on small dataset. |
| **Quantization** | 4-bit NF4 (bitsandbytes) | Reduces Mistral-7B from ~14GB -> ~4GB VRAM. Normal-float 4-bit preserves more precision than INT4. Double quantization further compresses. |
| **Max sequence length** | 512 tokens | Classification: input prompt (~200 tokens) + label (~5 tokens). 512 is generous headroom. |
| **Batch size** | 4 (x4 gradient accumulation = effective 16) | Effective batch of 16 stabilizes gradients for a 12-class problem without exceeding A100 memory. |
| **Learning rate** | 2e-4 with cosine schedule | Standard LoRA LR. Cosine decay prevents sudden jumps in later epochs. |
| **Warmup** | 10% of steps | Gradual warmup prevents early divergence on randomly initialized LoRA matrices. |
| **Epochs** | 3 | 3 epochs on 6.8K samples = ~1,275 steps. Enough to converge on structured classification; more risks overfitting. |
| **Early stopping** | load_best_model_at_end=True | Keeps checkpoint with lowest eval loss, protecting against late-epoch overfitting. |
| **Hardware adaptive** | CUDA -> MPS -> CPU fallback | Detects device at runtime. MPS (Apple Silicon) skips quantization (bitsandbytes doesn't support MPS). |

#### Inference Data Flow

```
New incident (structured fields)
    |
    v  format_incident_prompt(row)
    |
    v  Wrap in: <s>[INST] {SYSTEM_PROMPT}\n\n{incident} [/INST]
    |
    v  tokenize(max_length=512)
    |
    v  model.generate(max_new_tokens=20, temperature=0.1, do_sample=False)
    |          ^
    |     Greedy decoding -- deterministic, no randomness
    |     max_new_tokens=20 -- root cause labels are short ("oom", "image_pull")
    |
    v  Decode, strip special tokens
    |
    v  Output: "oom"
```

### Model 1 Evaluation Metrics

#### Classification-Specific Metrics

| Metric | Formula | Target | Actual Result |
|--------|---------|--------|---------------|
| **Fuzzy Accuracy** (primary) | Case-insensitive substring match: `correct / total x 100` | >=70% | **90.0%** (270/300) |
| **Exact Accuracy** | Case-sensitive exact string match | -- | Reported alongside |
| **Macro Precision** | Average of per-class `TP / (TP + FP)` | >=0.80 | From confusion matrix |
| **Macro Recall** | Average of per-class `TP / (TP + FN)` | >=0.80 | From confusion matrix |
| **Macro F1** | `2 x (P x R) / (P + R)` averaged across all 12 classes | >=0.85 | From confusion matrix |
| **Train/Val Loss Gap** | `val_loss - train_loss` per epoch | <0.3 | Monitored per epoch |

**Why fuzzy accuracy?** The LLM might output `"Out-of-Memory"` when the label is `"oom"` -- both are correct. Substring matching captures this without penalizing valid rephrasings.

#### Per-Class Accuracy Breakdown (Actual Results)

| Root Cause Family | Correct / Total | Accuracy | Training Samples |
|-------------------|-----------------|----------|-----------------|
| configmap | 12 / 12 | **100%** | 500 |
| connection | 14 / 16 | **88%** | 500 |
| image_pull | 34 / 34 | **100%** | 1,000 |
| missing_secret | 19 / 19 | **100%** | 500 |
| oom | 15 / 15 | **100%** | 500 |
| other | 7 / 20 | **35%** | 500 |
| probe | 38 / 38 | **100%** | 1,000 |
| pvc | 42 / 42 | **100%** | 1,000 |
| quota | 14 / 14 | **100%** | 500 |
| rbac | 3 / 18 | **17%** | 500 |
| scheduling | 72 / 72 | **100%** | 2,000 |

**8 out of 11 classes achieved 100% accuracy.** The failures cluster in `rbac` (17%) and `other` (35%) -- both have overlapping observability signals with `connection`.

#### Evaluation Visualizations (7 Plots Generated)
1. Data distribution (root cause counts + difficulty breakdown)
2. Training curves (per-step + per-epoch with overfitting gap)
3. Confusion matrix heatmap (12x12)
4. Per-class precision/recall/F1 bar chart
5. Per-class fuzzy accuracy with 70% target line
6. Accuracy by difficulty level (easy/medium/hard)
7. Validation summary dashboard

#### Known Failure Modes
- **`rbac` vs `connection` confusion (primary):** Both produce similar API rejection signals -- failed API calls, timeout events, rejected requests. Structured fields overlap significantly.
- **`other` class ambiguity:** Catch-all class with signals that overlap multiple categories. Model defaults to `connection` when uncertain.
- **`dns` class absence:** Only 2 samples in dataset -- effectively unlearnable.

### Tech Stack (Model 1)
- **PyTorch** + **Transformers** (HuggingFace): model loading, tokenization
- **PEFT**: LoRA adapter creation
- **TRL (SFTTrainer)**: supervised fine-tuning loop
- **bitsandbytes**: 4-bit NF4 quantization
- **pandas + PyArrow**: Parquet data loading
- **scikit-learn**: Isolation Forest, One-Class SVM, LOF (anomaly detection agent)
- **Google Colab (A100, 40GB VRAM)**: training environment

### Cost & Performance
- **Training**: ~15 min on A100
- **Inference**: <2s per incident
- **Cost**: $0/incident (self-hosted)
- **Agents**: 6

---

## Model 2: RAG-Enhanced Pipeline with Shared Knowledge

### Purpose
Add historical context to the pipeline. Answer: *"If we let the planner see similar past incidents, does accuracy improve?"* This is the first step toward knowledge-augmented RCA.

### Architecture: Model 1 + Retrieval Agent (7 Agents)

```
[M1 Pipeline: Ingestion -> Preprocessing -> Anomaly Detection]
    |
    v
[Retrieval Agent]  <---- FAISS Vector Index (built from agent2_evidence.parquet)
    |                     Embedding: sentence-transformers/all-MiniLM-L6-v2
    |                     Returns: top-5 similar past incidents + their diagnoses
    |
    v  raw evidence + RAG context
[Planner Agent]  -->  [Executor]  -->  [Reviewer]
 Qwen2.5-7B LoRA
 Generates: Diagnosis, Fix Plan,
 Actions, Verification, Rollback
```

### What's New: The Retrieval Agent

**ML Method:** Semantic vector search (not an LLM -- pure embedding + nearest-neighbor)

**How it works:**
1. **Index build time:** Encode all 8,502 `evidence_text` entries from `agent2_evidence.parquet` using `all-MiniLM-L6-v2` (384-dim embeddings) -> store in FAISS flat index
2. **Query time:** Encode the new incident's evidence text -> FAISS returns top-5 nearest neighbors with cosine similarity scores
3. **Output:** Past incidents' diagnoses and fix plans injected into the planner's prompt as context

**Why all-MiniLM-L6-v2?**
- 384 dimensions -- compact, fast
- Optimized for semantic similarity (trained on 1B+ sentence pairs)
- Runs locally, no API cost
- Good enough for K8s incident matching where keywords overlap heavily

**Why FAISS?**
- CPU-friendly (no GPU needed for vector search)
- Handles 8.5K vectors with negligible latency (<5ms)
- Well-supported Python library

### The Planner Agent -- How Model 2 Is Built

**File:** `agents/models/model2_qwen_lora.py` (375 lines)

#### Why Qwen2.5-7B-Instruct (not Mistral again)?

| Factor | Mistral-7B (M1) | Qwen2.5-7B (M2) | Why Qwen wins for M2 |
|--------|-----------------|------------------|----------------------|
| **Task** | Classification (1 label) | Generation (5-section report) | Generation needs stronger language modeling |
| **MMLU** | 62.5 | 74.2 | Qwen has significantly better general knowledge |
| **HumanEval** | 40.2 | 84.1 | 2x better at structured output generation |
| **GSM8K** | 52.2 | 85.4 | Better multi-step reasoning for fix plans |
| **ChatML** | No | Yes (native) | Qwen's native format supports multi-section structured output |
| **Long output** | Weak | Strong | Qwen handles 1024-token generation well |

Qwen is the better base for a generation task because it produces more coherent, structured, longer outputs.

#### Data Flow Through Training

```
agent2_evidence.parquet (8,502 rows, 16 columns)
    |
    v  build_evidence_prompt(): evidence_text + scenario_id + difficulty
    |
    v  build_target_response(): combine diagnosis/fix/actions/verification/rollback
    |
    v  build_chat_text(): wrap in ChatML format
    |
    v  Format:
    |   <|im_start|>system
    |   You are a Kubernetes SRE agent. Given raw observability evidence...
    |   <|im_end|>
    |   <|im_start|>user
    |   Analyze this Kubernetes incident and provide diagnosis and remediation:
    |
    |   namespace: backend-prod
    |   workload: api-server-abc12
    |   === kubectl describe pod ===
    |   Status: Running
    |   Reason: CrashLoopBackOff
    |   Exit Code: 137
    |   Restart Count: 14
    |   === container logs ===
    |   java.lang.OutOfMemoryError: Java heap space
    |   === metrics_snapshot ===
    |   restarts=14, oom_killed=True
    |   <|im_end|>
    |   <|im_start|>assistant
    |   ## Diagnosis
    |   Container OOM-killed due to memory limit too low...
    |   ## Fix Plan
    |   1. Increase memory limits to 512Mi...
    |   ## Actions
    |   [kubectl patch] kubectl patch deployment api-server...
    |   ## Verification
    |   Check pod restarted successfully...
    |   ## Rollback
    |   Revert memory limit to 256Mi
    |   <|im_end|>
    |
    v  train_test_split(test_size=0.2, seed=42)
    |
    +---> 6,801 train
    +---> 1,701 test
```

#### Key Differences from Model 1 Training

| Setting | Model 1 | Model 2 | Why Different |
|---------|---------|---------|---------------|
| **Max seq len** | 512 | **1024** | Generation output is 5 sections long (~400-700 tokens) |
| **LoRA targets** | `q,k,v,o_proj` | `q,k,v,o_proj` + **`gate_proj, up_proj, down_proj`** | Generation needs MLP layers too -- they control how the model "writes" new text, not just "reads" input |
| **Batch size** | 4 (x4 accum) | **2 (x8 accum)** | Longer sequences = more VRAM per sample; same effective batch size (16) |
| **Inference temp** | 0.1 (greedy) | **0.3 (sampling)** | Generation benefits from slight diversity; pure greedy produces repetitive text |
| **top_p** | -- | **0.9** | Nucleus sampling keeps generation coherent while allowing variation |
| **repetition_penalty** | -- | **1.1** | Prevents "## Diagnosis\n## Diagnosis\n..." loops in long generation |
| **max_new_tokens** | 20 | **512** | Classification needs ~5 tokens; generation needs ~400 |

#### Inference Data Flow (with RAG)

```
New incident (raw evidence text)
    |
    +---> [Retrieval Agent]
    |     embed(evidence_text) -> FAISS.search(k=5)
    |     Returns: 5 similar past incidents with diagnoses
    |
    v  Construct prompt:
    |   <|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>
    |   <|im_start|>user\n
    |   Analyze this Kubernetes incident...
    |
    |   {evidence_text}
    |
    |   Similar past incidents:
    |   1. [OOM in backend-prod] -> "Memory limit too low, increased to 512Mi"
    |   2. [OOM in data-pipeline] -> "Memory leak in batch processor"
    |   ...
    |   <|im_end|>
    |   <|im_start|>assistant\n
    |
    v  model.generate(max_new_tokens=512, temp=0.3, top_p=0.9)
    |
    v  Output:
        ## Diagnosis
        Container OOM-killed due to memory limit set to 256Mi...
        ## Fix Plan
        1. Increase memory limit to 512Mi
        2. Monitor heap usage for leaks
        ## Actions
        kubectl patch deployment api-server --patch '{"spec":...}'
        ## Verification
        kubectl get pods -w | grep api-server
        ## Rollback
        kubectl rollout undo deployment/api-server
```

### Model 2 Evaluation Metrics

#### Generation-Specific Metrics

| Metric | Formula | Weight | Target |
|--------|---------|--------|--------|
| **Structure Score** | `sections_found / 3` where sections = {Diagnosis, Fix Plan, Verification} | 40% | -- |
| **Keyword Overlap** | `|GT_keywords intersection Pred_keywords| / |GT_keywords|` (words >4 chars) | 60% | -- |
| **Combined Score** (primary) | `0.4 x structure + 0.6 x keyword_overlap` | 100% | >=70% |
| **Train/Val Loss Gap** | `val_loss - train_loss` per epoch | -- | <0.5 (higher tolerance for generation) |

**Why not BLEU/ROUGE?** They penalize valid paraphrases. "The container ran out of memory" and "OOM-killed due to insufficient memory limits" say the same thing but score low on BLEU. Keyword overlap captures whether the right **technical concepts** (OOMKilled, 512Mi, memory, restart) are present regardless of phrasing.

**Why structure score?** An SRE needs actionable sections, not a wall of text. If the model outputs a correct diagnosis but doesn't label it `## Diagnosis`, it's less useful operationally.

#### Additional Generation Quality Metrics

| Metric | What It Measures | Why It Matters |
|--------|-----------------|----------------|
| **Per-Scenario Scores** | Combined score broken down by scenario_id | Identifies which K8s failure types the model struggles to explain |
| **Score by Difficulty** | Combined score by easy/medium/hard | Confirms model degrades gracefully on harder incidents |
| **Response Length** | Token count of generated output (target: 100-700) | Too short = incomplete sections; too long = repetitive filler |
| **Section Completeness** | Which of the 5 sections (Diagnosis/Fix/Actions/Verification/Rollback) are most often missing | Reveals systematic generation failures |

#### Evaluation Protocol
1. **Train/Val Split:** 80/20 random split (seed=42)
2. **Overfitting Check:** Per-epoch train vs. val loss comparison; flagged if gap >0.5
3. **Inference Evaluation:** Run on up to 200 held-out samples with greedy decoding (`do_sample=False`, `repetition_penalty=1.1`)
4. **Target:** Combined score >=70%

#### Anti-Overfitting Regularization

| Technique | Setting | Purpose |
|-----------|---------|---------|
| LoRA rank | 16 (code) / 32 (notebook) | Constrains adapter capacity |
| LoRA dropout | 0.05-0.1 | Forces distributed knowledge across parameters |
| Weight decay | 0.01-0.05 | L2 regularization penalizes large weights |
| Early stopping | patience=2 | Primary overfitting defense |
| Cosine LR schedule | 2e-4 -> 0 | Reduces late-epoch overfitting |
| Max sequence length | 1024 | Caps input to prevent memorizing long sequences |

#### Evaluation Visualizations (7 Plots)
1. Data distribution (scenario counts + difficulty)
2. Training curves (per-step + per-epoch with overfitting gap)
3. Score distribution histogram (combined scores across all eval samples)
4. Score component breakdown (structure vs. keyword contribution)
5. Per-scenario scores (which failure types are hardest to explain?)
6. Difficulty vs. score (easy/medium/hard breakdown)
7. Response length analysis (distribution of output lengths)

### Tech Stack (Model 2 additions)
- Everything from Model 1, plus:
- **sentence-transformers** (`all-MiniLM-L6-v2`): embedding model for RAG
- **FAISS**: CPU vector index for nearest-neighbor retrieval

### Cost & Performance
- **Training**: ~25 min on A100 (longer sequences)
- **Inference**: <5s per incident (embedding + FAISS + generation)
- **Cost**: $0/incident (all local)
- **Agents**: 7

---

## Model 3: SMART-Inspired Multi-Step Reasoning with Blackboard Architecture

### Purpose
Handle complex incidents that M1/M2 get wrong (ambiguous dns<->connection, multi-signal scheduling failures). Answer: *"Given powerful LLMs with tool access and explicit validation, what's the best achievable RCA accuracy?"*

### Architecture: Blackboard + Multi-Step Validation (9 Agents)

```
[M1/M2 Agents: Ingestion -> Preprocessing -> Anomaly Detection]
    |
    v
+----------------- BLACKBOARD (Shared State Dict) -----------------+
|                                                                    |
|  [Intent Classifier]  (NEW)                                        |
|    LLM: GPT-4o-mini or Claude Haiku                                |
|    Reads: raw incident                                             |
|    Writes: intent = {                                              |
|      incident_type: "crash_loop",                                  |
|      severity: "high",                                             |
|      affected_components: ["api-server"],                          |
|      blast_radius: "namespace"                                     |
|    }                                                               |
|         |                                                          |
|  [Retrieval Agent]  (inherited from M2)                            |
|    Reads: intent from blackboard                                   |
|    Writes: retrieved_context = [top-5 similar + confidence scores] |
|         |                                                          |
|  [Validation Agent]  (NEW)                                         |
|    LLM: GPT-4o or Claude Sonnet                                    |
|    Tools: get_pod_status(), get_events(), get_metrics(), get_logs() |
|    Reads: intent + retrieved_context                               |
|    Writes: validation_result = {                                   |
|      evidence_verified: true/false,                                |
|      hallucinations_detected: [...],                               |
|      confidence: 0.85,                                             |
|      flag_for_manual_review: false                                 |
|    }                                                               |
|         |                                                          |
|  [Planner Agent]  (ENHANCED)                                       |
|    LLM: Claude Opus 4 or GPT-4o                                    |
|    Reads: ENTIRE blackboard (intent + context + validation)        |
|    Writes: rca_plan = {                                            |
|      root_cause: "oom",                                            |
|      diagnosis: "...",                                             |
|      fix_plan: [...],                                              |
|      verification_steps: [...],                                    |
|      confidence: 0.92                                              |
|    }                                                               |
|                                                                    |
+--------------------------------------------------------------------+
    |
    v
[Executor Agent]  -->  [Reviewer Agent]
```

### What's New: The Blackboard Pattern

**Why a blackboard instead of message passing?**

In M1/M2, each agent only sees the previous agent's output. If the Retrieval Agent returns bad matches, the Planner has no way to verify -- it just trusts the input. The **blackboard** solves this by:

1. **All agents read/write to the same shared state dict** -- every agent sees everything
2. **Multi-step validation before final decision** -- the Validation Agent checks the Retrieval Agent's work before the Planner uses it
3. **Full audit trail** -- every write is timestamped with the agent that wrote it, creating an auditable decision trajectory

### New Agent: Intent Classifier

**LLM: GPT-4o-mini** (for speed/cost) or **Claude Haiku** (alternative)

**Why these LLMs?** Intent classification is a lightweight task -- categorize the incident type and severity. It doesn't need deep reasoning. GPT-4o-mini (MMLU 82.0, Function Calling 92%) is the best cost/performance balance for a fast classification step. Haiku is the alternative for teams preferring Anthropic.

**Reasoning behind this agent:** Without intent classification, the Retrieval Agent searches blindly. With it, the query becomes targeted: "Find past scheduling incidents in production with high severity" instead of "find similar incidents." This dramatically improves RAG precision.

### New Agent: Validation Agent

**LLM: GPT-4o** (best tool-calling model) or **Claude Sonnet** (alternative)

**Why GPT-4o / Sonnet?** This agent needs to:
1. **Call tools** (simulated K8s queries) -- GPT-4o has 94%+ function calling accuracy
2. **Cross-reference** tool results with retrieved context -- requires multi-step reasoning
3. **Detect hallucinations** -- requires comparing claims against evidence

These are harder tasks than intent classification, justifying a stronger (more expensive) model.

**Tool definitions** (simulated -- look up data from the original JSONL):
- `get_pod_status(incident_id)` -> returns parsed kubectl describe
- `get_events(incident_id)` -> returns K8s events
- `get_metrics(incident_id)` -> returns metrics snapshot
- `get_logs(incident_id)` -> returns container logs

**Why simulated tools?** For evaluation, we use the synthetic dataset's ground truth as the "K8s cluster." In production, these would be real kubectl calls.

### Enhanced Planner

**LLM: Claude Opus 4** (strongest reasoning) or **GPT-4o** (alternative)

**Why the strongest model here?** The planner is the final decision-maker. It reads the entire validated blackboard and synthesizes the root cause determination. This is the highest-stakes reasoning step -- an error here propagates to execution. Claude Opus 4 (MMLU ~92, MT-Bench 9.5+) has the best multi-step logical reasoning and meta-cognition capabilities of any available model.

### Data Flow Example

```
Incident: "Pod in Pending state, keeps getting rescheduled"

Step 1 -- Intent Classifier (GPT-4o-mini, ~$0.001):
  Input: raw incident text
  Output -> blackboard.intent:
    {type: "scheduling", severity: "high",
     affected: ["api-server"], blast_radius: "cluster"}

Step 2 -- Retrieval Agent (FAISS, ~$0):
  Input: reads blackboard.intent -> queries "scheduling failures in production"
  Output -> blackboard.retrieved_context:
    [{diagnosis: "NodeSelector mismatch", fix: "update selector"},
     {diagnosis: "Taint not tolerated", fix: "add toleration"}, ...]

Step 3 -- Validation Agent (GPT-4o, ~$0.03):
  Input: reads intent + retrieved_context
  Tool calls: get_events("incident_123") -> "FailedScheduling: 0/3 nodes matched"
              get_pod_status("incident_123") -> "Pending, no matching node"
  Cross-checks: "Yes, retrieved context matches. NodeSelector mismatch confirmed."
  Output -> blackboard.validation_result:
    {evidence_verified: true, hallucinations_detected: [],
     confidence: 0.88, flag_for_manual_review: false}

Step 4 -- Planner (Claude Opus 4, ~$0.08):
  Input: reads ENTIRE blackboard
  Output -> blackboard.rca_plan:
    {root_cause: "scheduling",
     diagnosis: "NodeSelector mismatch -- pod requires zone=us-east-1a but...",
     fix_plan: ["Update nodeSelector", "Verify node labels"],
     verification: ["kubectl get pods -w"],
     confidence: 0.92}
```

### ML Methods in Model 3

| Component | ML Method | Purpose |
|-----------|-----------|---------|
| Anomaly Detection | Isolation Forest, One-Class SVM, LOF | Statistical anomaly scoring (inherited from M1) |
| Retrieval Agent | Sentence embeddings + FAISS kNN | Semantic similarity search (inherited from M2) |
| Intent Classifier | LLM prompting (few-shot) | Zero/few-shot classification -- no fine-tuning needed at this tier |
| Validation Agent | LLM + tool calling | Grounded verification with evidence retrieval |
| Planner | LLM prompting with full context | Multi-step reasoning over validated evidence |

**Key insight:** Model 3 shifts from **fine-tuned small models** (M1/M2) to **prompted large models** (GPT-4o, Opus). This is because:
- Complex reasoning (validation, multi-step synthesis) benefits more from scale than from domain-specific fine-tuning
- Fine-tuning is only cost-effective when you have >1000 examples of the exact task format
- Tool calling and multi-step validation aren't easily teachable through SFT

### Model 3 Evaluation Metrics

#### Validated Reasoning Metrics

| Metric | Formula / Method | Target |
|--------|-----------------|--------|
| **RCA Accuracy** (primary) | Fuzzy match of `blackboard.rca_plan.root_cause` vs. ground truth | >=75% |
| **Trajectory Coherence** | Evaluator LLM scores 0-1: does each blackboard write logically follow from prior state? | >=0.80 |
| **Hallucination Detection Rate** | `hallucinations_caught / hallucinations_injected x 100` | >=80% |
| **Evidence Validation Accuracy** | `correct_validation_judgments / total_validations x 100` | >=85% |
| **Tool-Call Accuracy** | `correct_tool_calls / total_tool_calls x 100` (correct parameters + correct interpretation of results) | >=90% |
| **Blackboard Utilization** | `fields_used_by_downstream / total_fields_written x 100` | >=70% |
| **Intent Classification Accuracy** | `correct_intents / total_incidents x 100` | >=85% |
| **Confidence Calibration (ECE)** | Expected Calibration Error: `SUM |accuracy_bin - confidence_bin| x bin_weight` | <0.10 |

**Why trajectory coherence?** A model that gets the right answer for the wrong reason is fragile. If the Validation Agent says "verified" but didn't actually check the evidence, the audit trail is meaningless. Trajectory coherence checks that each step's output is justified by its inputs.

**How hallucination detection is measured:** Inject known bad context (incorrect past incidents from a different scenario) into the Retrieval Agent's output. Count how often the Validation Agent catches it vs. lets it pass through to the Planner.

**Why confidence calibration (ECE)?** If the model says "confidence: 0.9" it should be correct ~90% of the time. Poor calibration means confidence scores are meaningless and the routing strategy (escalate to M4 when confidence <70%) breaks down.

#### Evaluation Protocol
1. Run on same 500 held-out incidents as M1/M2
2. For hallucination testing: inject 100 incidents with deliberately wrong retrieved context
3. Score trajectory coherence using a separate evaluator LLM that reads the full blackboard trace
4. Compute ECE by binning incidents into 10 confidence buckets and comparing average confidence vs. average accuracy per bin

### Tech Stack (Model 3 additions)
- Everything from M1/M2, plus:
- **OpenAI API** (`openai` SDK): GPT-4o-mini, GPT-4o
- **Anthropic API** (`anthropic` SDK): Claude Haiku, Sonnet, Opus 4
- **Async message bus**: For blackboard read/write coordination
- **Tool/function calling**: OpenAI function calling or Anthropic tool use
- **Mock LLM client**: For development/testing without API keys

### Cost & Performance
- **Cost**: $0.08-0.15/incident (API calls)
- **Latency**: <10s (tool calls add latency)
- **Agents**: 9 (6 inherited + Intent Classifier, Validation Agent, enhanced Planner)

---

## Model 4: Self-Evolving Cognitive Hybrid MAS (Multi-Agent Debate)

### Purpose
Handle the hardest 5-10% of incidents where even M3 is uncertain. Answer: *"How close can a MAS get to being a self-improving, safe co-SRE?"* This model introduces debate for robustness, safety gates for production use, and memory for continuous improvement.

### Architecture: Model 3 Output -> Debate -> Referee -> Safety -> Memory (14 Agents)

```
[Model 3 produces blackboard state]
    |
    v
+------------ DEBATE PROTOCOL (3 rounds) ------------+
|                                                      |
|  Round 1: Independent Hypothesis Generation          |
|  +-------------+-------------+-------------+        |
|  | Debater 1   | Debater 2   | Debater 3   |        |
|  | Claude Opus | GPT-4o      | DeepSeek-R1 |        |
|  | Analytical  | Practical   | Contrarian  |        |
|  +------+------+------+------+------+------+        |
|         |             |             |                |
|  Round 2: Cross-Examination                          |
|  Each reads others' hypotheses -> produces counters  |
|         |             |             |                |
|  Round 3: Refined Final Arguments                    |
|  Each incorporates counter-evidence -> final position|
|         |             |             |                |
+---------+-------------+-------------+                |
                    |                                   |
                    v                                   |
            [Referee Agent]                             |
             Claude Opus 4 / GPT-4o                     |
             Scores: evidence 35%, logic 25%,           |
             specificity 20%, resilience 20%            |
             NOT majority vote -- quality-based         |
                    |                                   |
                    v                                   |
            [Safety Agent (HiTL)]                       |
             GPT-4o-mini / Claude Haiku                 |
             PASS: restart, scale up, configmap         |
             WARN: kubectl patch in prod (30s delay)    |
             BLOCK: delete, drain, RBAC, scale to 0    |
                    |                                   |
                    v                                   |
            [Executor] -->  [Reviewer]                  |
                    |                                   |
                    v                                   |
            [Context Memory Manager]                    |
             FAISS + JSON log                           |
             Stores: incident, hypotheses, winner,      |
             scores, outcome -> future RAG context      |
```

### Why 3 Different LLMs for Debaters?

This is the core innovation. Each LLM has a genuinely different reasoning style:

| Debater | LLM | Reasoning Style | Why This LLM |
|---------|-----|----------------|---------------|
| **Debater 1 (Analytical)** | Claude Opus 4 | Deep logical chains, meta-cognition, considers edge cases carefully | Opus excels at "thinking about thinking" -- it naturally identifies assumptions and questions its own reasoning |
| **Debater 2 (Practical)** | GPT-4o | Balanced breadth/depth, action-oriented, strong planning | GPT-4o is optimized for structured output and planning -- it produces the most actionable fix plans |
| **Debater 3 (Contrarian)** | DeepSeek-R1 or Llama 3 70B | Different training data/approach, challenges consensus | Trained on different data distributions. DeepSeek-R1 uses chain-of-thought differently, catching blind spots the others share |

**Why diversity matters:** If all 3 debaters use the same LLM, they'll make the same mistakes (shared training biases). Heterogeneous models reduce **groupthink**: if 2 models share a blind spot, the 3rd often catches it. This is analogous to ensemble methods in ML -- diversity reduces correlated errors.

### The Referee Agent

**LLM: Claude Opus 4 or GPT-4o** (strongest available)

**Why NOT majority vote?** Majority vote treats all arguments equally. But a well-evidenced minority argument should win over two poorly-reasoned majority ones. The referee scores on 4 dimensions:

| Dimension | Weight | What It Rewards |
|-----------|--------|-----------------|
| **Evidence Grounding** | 35% | Claims backed by specific kubectl output, logs, metrics |
| **Logical Coherence** | 25% | Reasoning chain holds up, no logical gaps |
| **Remediation Specificity** | 20% | Fix plan is actionable (specific commands, not "check the logs") |
| **Resilience to Counter-Arguments** | 20% | Argument survived cross-examination without collapsing |

### The Safety Agent (Human-in-the-Loop)

**LLM: GPT-4o-mini or Claude Haiku** (fast, cheap -- speed matters for gating)

**Why a separate safety agent?** The Planner optimizes for correctness. The Safety Agent optimizes for risk. These are fundamentally different objectives -- combining them in one agent creates conflicting priorities.

**Three-tier autonomy framework:**

| Tier | Level | Actions | Human Role |
|------|-------|---------|------------|
| **Tier 1** | Fully Autonomous | All read-only agents (Debaters, Referee, Intent Classifier, Retrieval, Validation, Memory Manager) | Reviews after the fact |
| **Tier 2** | Semi-Autonomous | Planner, Executor (low/medium risk), Memory writes | 30-second override window |
| **Tier 3** | Human-in-the-Loop | Executor (high risk), Safety-blocked actions | Explicit approval required in Kibana dashboard |

**Never-autonomous actions** (hardcoded policy):
- `kubectl delete` pods/PVCs/secrets
- `kubectl drain --force`
- Scale to 0 replicas
- Modify RBAC
- Any action on confidence <50%

### The Context Memory Manager

**ML Method: Embedding + FAISS + JSON append log** (no LLM -- deterministic indexing)

**How self-improvement works:**

```
Phase 1 (Early, incidents 1-50):
  All debaters equally credible
  Memory stores: incident, winner, scores

Phase 2 (After 50+ incidents):
  Memory queries: "Debater 1 has won 68% of security incidents"
  New security incident -> Debaters receive context:
    "Historical note: In 47 similar security incidents,
     the analytical approach (Debater 1) was most accurate.
     Past misdiagnoses were due to: ignoring IAM policy changes."

Phase 3 (After 100+ incidents):
  System tracks:
  - Per-debater win rate by incident type
  - Error-repeat rate (same mistake twice? Target: <5%)
  - Human override patterns (SRE keeps correcting a specific debater)
  - Recurring failure patterns -> pre-cached diagnoses
```

**Storage format per resolved incident:**
```json
{
  "incident_id": "...",
  "scenario_id": "crashloop_oomkilled_limit_too_low",
  "evidence_summary": "Exit code 137, restart count 14, memory 256Mi",
  "winning_rca": "Container OOM-killed due to memory limit too low",
  "winning_debater": "Debater 1 (Opus)",
  "debater_scores": {"D1": 92, "D2": 78, "D3": 65},
  "outcome": "fixed",
  "human_override": false,
  "timestamp": "2026-03-22T12:00:00Z"
}
```

### Full Data Flow Example

```
Incident: "Pod CrashLoopBackOff, logs show OutOfMemoryError, restarted only once"

[M3 Blackboard already contains:]
  intent: {type: "crash_loop", severity: "high"}
  retrieved_context: [{diagnosis: "OOM limit too low"}, ...]
  validation_result: {verified: true, confidence: 0.85}

Round 1 -- Independent Hypotheses:
  Debater 1 (Opus, analytical):
    "OOM from memory LEAK. Evidence: Error message + exit code 137.
     But only 1 restart suggests the leak is slow.
     Fix: Add JVM -Xmx flag + investigate heap dump."
    Confidence: 0.90

  Debater 2 (GPT-4o, practical):
    "OOM from limit too low. Evidence: Container spec only 256Mi.
     Fix: Increase to 512Mi."
    Confidence: 0.85

  Debater 3 (DeepSeek-R1, contrarian):
    "NOT OOM -- startup failure with misleading error.
     Evidence: Only 1 restart (OOM usually causes rapid cycling).
     Fix: Check startup probe timeout."
    Confidence: 0.75

Round 2 -- Cross-Examination:
  D1: "D3 is wrong -- if it were startup, we'd see probe failure events, not OOM error"
  D2: "D1's memory leak theory is unsupported -- no heap dump evidence"
  D3: "D2 oversimplifies -- both memory limit AND startup issue could coexist"

Round 3 -- Refined Final Arguments:
  D1 incorporates D2's point: "Likely limit too low, not leak -- but recommend monitoring"
  D2 holds position with stronger evidence citations
  D3 concedes partial point but maintains startup should be checked

Referee Scoring:
  D1: evidence=92, logic=90, specificity=88, resilience=85 -> 89/100
  D2: evidence=88, logic=80, specificity=90, resilience=75 -> 84/100
  D3: evidence=65, logic=70, specificity=60, resilience=55 -> 63/100
  Winner: Debater 1 (analytical deep-dive wins)

Safety Agent:
  Proposed: "Increase memory limit to 512Mi"
  Policy check: PASS (config change, non-destructive)
  Auto-approve

Memory Manager:
  Index: incident + D1 won (score 89) + outcome pending
  [After executor confirms fix]: outcome = "fixed"
  Future similar incidents will see: "D1 won last 3 OOM incidents (89, 91, 87)"
```

### Model 4 Evaluation Metrics

#### Debate & Consensus Metrics

| Metric | Formula / Method | Target |
|--------|-----------------|--------|
| **RCA Accuracy** (primary) | Fuzzy match of referee's selected root cause vs. ground truth | >=80% |
| **Accuracy Gain from Debate** | `M4_accuracy - M3_accuracy` on same 500 incidents | +5-10% |
| **Debate Diversity** | Jaccard distance between debaters' hypotheses: `1 - |D1 intersect D2 intersect D3| / |D1 union D2 union D3|` | >0.3 |
| **Quality vs. Majority Divergence** | % of times highest-scored argument is NOT the majority pick | >10% |
| **Referee Agreement Rate** | % of times referee picks the same root cause as majority of debaters | Track (no target) |

**Why debate diversity >0.3?** If all 3 debaters produce the same hypothesis, debate adds cost but no value. Jaccard distance <0.3 means the debaters are too similar -- the design choice of using 3 different LLMs isn't paying off.

**Why quality vs. majority divergence >10%?** This validates the referee's evidence-based scoring over majority vote. If the minority *never* wins, the referee is just a fancy vote counter.

#### Safety & Autonomy Metrics

| Metric | Formula / Method | Target |
|--------|-----------------|--------|
| **Safety Violation Rate** | `dangerous_actions_not_caught / total_dangerous_actions` | **0%** (zero tolerance) |
| **False Escalation Rate** | `safe_actions_blocked / total_safe_actions x 100` | <10% |
| **Human Override Rate** | `SRE_overrides / total_decisions x 100` | 5-15% |

**Why human override 5-15%?** Too low (<5%) suggests SREs are rubber-stamping -- the safety gate isn't being taken seriously. Too high (>15%) means the system isn't accurate enough to be useful.

#### Self-Improvement Metrics

| Metric | Formula / Method | Target |
|--------|-----------------|--------|
| **Error-Repeat Reduction** | `repeated_misclassifications / total_misclassifications` over successive batches | <5% after 50 incidents |
| **Self-Improvement Curve** | Plot accuracy per batch of 10 incidents over time | Monotonically increasing |
| **Memory Retrieval Precision** | `relevant_retrieved / total_retrieved x 100` | >70% |
| **Debater Win Rate by Type** | Per-incident-type win rate per debater (tracks specialization emergence) | Track over time |

**Why error-repeat reduction?** The whole point of the Memory Manager is that the system doesn't make the same mistake twice. If it keeps misclassifying the same failure type after seeing 50 examples, the memory system isn't working.

#### Evaluation Protocol
1. Run on same 500 held-out incidents as M1/M2/M3
2. For safety testing: inject 50 incidents with deliberately dangerous proposed remediations (delete, drain, scale-to-zero)
3. For self-improvement: run 200 incidents sequentially (not in parallel) and track accuracy trajectory
4. For debate diversity: compute pairwise Jaccard distance between all debater pairs across all incidents

### Tech Stack (Model 4 additions)
- Everything from M1/M2/M3, plus:
- **DeepSeek API** or **self-hosted Llama 3 70B**: Debater 3
- **Debate orchestrator**: manages 3 rounds of structured argumentation
- **Safety policy engine**: hardcoded rules + LLM classification
- **JSON append log**: incident outcome storage for memory
- **Debater performance tracker**: per-incident-type win rate ledger

### Cost & Performance
- **Cost**: $0.30-0.60/incident (3 debaters x 3 rounds + referee + safety)
- **Latency**: <20s (debate rounds dominate)
- **Agents**: 14 (9 from M3 + Debaters x3, Referee, Safety Agent, Memory Manager)

---

## Cross-Model Evaluation: Comparing All 4 Models Head-to-Head

### Unified RCA Accuracy (The Primary Comparison Axis)

All models ultimately produce a root cause determination. We normalize to a single comparable metric:

| Model | How RCA is Extracted | Comparison Method | Target | Actual |
|-------|---------------------|-------------------|--------|--------|
| M1 | Direct label output (`"oom"`) | Fuzzy match vs. ground truth | >=70% | **90.0%** |
| M2 | Extract root cause keyword from `## Diagnosis` section | Fuzzy match + keyword overlap | >=70% | _(from Colab)_ |
| M3 | `blackboard.rca_plan.root_cause` | Fuzzy match vs. ground truth | >=75% | _(planned)_ |
| M4 | Referee's selected winning hypothesis root cause | Fuzzy match vs. ground truth | >=80% | _(planned)_ |

### Per-Root-Cause Accuracy Comparison (Most Diagnostic View)

This reveals exactly **where** each model adds value:

| Root Cause Family | M1 (Actual) | M2 (Expected) | M3 (Expected) | M4 (Expected) | Why Improvement |
|-------------------|-------------|---------------|---------------|---------------|-----------------|
| configmap | **100%** | ~100% | ~100% | ~100% | Unique signal: CreateContainerConfigError |
| image_pull | **100%** | ~100% | ~100% | ~100% | Distinct error messages in events |
| scheduling | **100%** | ~100% | ~100% | ~100% | Clear FailedScheduling events |
| probe | **100%** | ~100% | ~100% | ~100% | Distinct liveness/readiness signals |
| pvc | **100%** | ~100% | ~100% | ~100% | Unambiguous mount failure events |
| missing_secret | **100%** | ~100% | ~100% | ~100% | Unique secret-not-found event |
| quota | **100%** | ~100% | ~100% | ~100% | Explicit quota exceeded message |
| connection | **88%** | ~90% | ~95% | ~98% | RAG provides historical disambiguation |
| oom | **100%** | ~100% | ~100% | ~100% | Strong signal: exit code 137 |
| **rbac** | **17%** | ~40% | ~75% | ~85% | **Most improved class** -- RAG + validation + debate resolve rbac<->connection overlap |
| **other** | **35%** | ~45% | ~60% | ~70% | Catch-all improved by debate generating alternatives |
| dns | N/A (2 samples) | ~50% | ~70% | ~80% | Ambiguous with connection -- multi-signal reasoning needed |

**Key insight:** 8/11 classes are already at 100% with M1. The entire justification for M2->M3->M4 rests on improving `rbac`, `other`, `dns`, and `connection` -- the classes with **overlapping observability signals**.

### Operational Metrics Comparison

| Metric | M1 | M2 | M3 | M4 |
|--------|----|----|----|----|
| **Cost per Incident** | $0.00 | $0.00 | $0.08-0.15 | $0.30-0.60 |
| **Latency (p95)** | <2s | <5s | <10s | <20s |
| **Throughput (incidents/min)** | ~30 | ~12 | ~6 | ~3 |
| **Agent Count** | 6 | 7 | 9 | 14 |
| **Requires API Keys** | No | No | Yes | Yes |
| **Self-Improving** | No | No | No | Yes |
| **Hallucination Risk** | Medium | Medium | Low | Very Low |

### Cost-Efficiency Frontier

Plot **RCA Accuracy (y-axis)** vs **Cost per Incident (x-axis)**:

```
Accuracy
  100% |                                          * M4
       |                                    * M3
   90% |  * M1
       |       * M2
   80% |
       |
   70% | - - - - - - - - - - - Target Line - - -
       |
   60% |
       +------------------------------------------
       $0.00  $0.05  $0.10  $0.20  $0.30  $0.50
                     Cost per Incident
```

Models on the **Pareto frontier** (upper-left envelope) are cost-efficient. The routing strategy exploits this: use the cheapest model that meets the confidence threshold.

### Statistical Significance Tests

Raw accuracy numbers aren't enough -- you need to know if differences are real:

#### Friedman Test (Non-Parametric ANOVA)
- **Question:** Are all 4 models significantly different?
- **Method:** For each of 500 held-out incidents, rank models by correctness. Compute Friedman chi-squared.
- **Significance:** p < 0.05 means at least one model is significantly different.
- **Why Friedman?** Data is ordinal (correct/incorrect), not normally distributed.

#### McNemar's Test (Pairwise)
- **Question:** Is Model X significantly better than Model Y?
- **Method:** For each pair (6 comparisons: M1vM2, M1vM3, M1vM4, M2vM3, M2vM4, M3vM4), build 2x2 contingency table:

```
                    Model B Correct    Model B Wrong
Model A Correct  |  a (both right)   |  b (A right, B wrong)  |
Model A Wrong    |  c (A wrong, B right) |  d (both wrong)    |
```

McNemar's chi-squared = `(b - c)^2 / (b + c)`. With **Bonferroni correction** for 6 tests: significance threshold = `0.05 / 6 = 0.0083`.

#### Cohen's h (Effect Size)
Even if p < 0.05, the difference might be trivially small:

```
h = 2 x arcsin(sqrt(p1)) - 2 x arcsin(sqrt(p2))
```

| h value | Interpretation |
|---------|---------------|
| <0.2 | Negligible -- cost of M3/M4 isn't justified |
| 0.2-0.5 | Small but real -- justify if M3/M4 fix specific failure types |
| 0.5-0.8 | Medium -- clear benefit from escalation |
| >0.8 | Large -- M3/M4 dramatically outperform |

#### Per-Class McNemar's Test
Run McNemar's **per root_cause_family** to identify exactly which failure types benefit from escalation:

```
For each root_cause_family (oom, rbac, dns, ...):
    For each model pair (M1vM2, M2vM3, M3vM4):
        Build 2x2 table on just that class's incidents
        Compute McNemar's chi-squared + p-value + Cohen's h
```

This produces a matrix showing: "M3 is significantly better than M2 on `rbac` (p=0.001, h=0.65) but not on `oom` (p=0.99, h=0.00)."

### Qualitative Evaluation: SRE Expert Review

For 50 random incidents, have an SRE rate each model's output:

| Dimension | Scale | What It Measures |
|-----------|-------|-----------------|
| **Correctness** | 1-5 | Is the root cause identification correct? |
| **Actionability** | 1-5 | Could an SRE execute this fix plan right now? |
| **Completeness** | 1-5 | Are all relevant aspects covered (diagnosis, fix, verification, rollback)? |
| **Trustworthiness** | 1-5 | Would you trust this without double-checking? |
| **Explanation Quality** | 1-5 | Does the reasoning make sense? Is it auditable? |

**Inter-annotator agreement:** Cohen's kappa between 2+ SRE reviewers. Target: kappa > 0.6.

**Expected pattern:**
- M1 scores high on Correctness (90%) but low on Completeness (only a label, no fix plan)
- M2 scores high on Completeness (5 sections) but may have lower Trustworthiness (hallucinations)
- M3 scores high on Trustworthiness (validated reasoning) and Explanation Quality (audit trail)
- M4 scores highest on all dimensions but costs 100x more than M1

### Error Analysis Taxonomy

Categorize all misclassifications across all models:

| Error Type | Description | Most Affected Model | How Later Models Fix It |
|------------|-------------|--------------------|-----------------------|
| **Signal Overlap** | Two root causes produce identical observability signals | M1 (`rbac<->connection`) | M2: RAG provides historical disambiguation. M3: Validation Agent cross-checks with tools. M4: Debate surfaces alternative explanations. |
| **Catch-All Ambiguity** | `other` absorbs edge cases | M1 (35% on `other`) | M2: RAG matches to specific past incidents. M4: Contrarian debater challenges the catch-all. |
| **Data Scarcity** | Too few training examples | M1 (`dns` = 2 samples) | M3/M4: API models don't need fine-tuning; zero-shot reasoning handles rare classes. |
| **Hallucination** | Model invents evidence not present | M2 (keyword miss) | M3: Validation Agent detects unsupported claims. M4: Cross-examination catches hallucinations. |
| **Context Missing** | Correct reasoning, wrong answer due to missing info | M1 (no historical context) | M2: RAG adds context. M3: Tools query actual cluster state. |
| **Debate Failure** | All debaters converge on wrong answer | M4 (rare) | Track frequency -- if >5%, debate diversity is insufficient. |

### Complete Evaluation Summary Matrix

| Metric Category | Metric | M1 | M2 | M3 | M4 |
|----------------|--------|----|----|----|----|
| **Primary Accuracy** | Unified RCA Accuracy (fuzzy) | **90.0%** | >=70% | >=75% | >=80% |
| **Primary Accuracy** | Model-specific metric | Fuzzy Acc | Combined Score | Trajectory + RCA | Debate consensus |
| **Precision/Recall** | Macro F1 | From confusion matrix | Via keyword overlap | Via RCA match | Via debate winner |
| **Robustness** | Hallucination Rate | Unmeasured | Keyword miss % | Validation catch >=80% | Debate correction rate |
| **Robustness** | Confidence Calibration (ECE) | -- | -- | <0.10 | <0.10 |
| **Efficiency** | Cost per Incident | $0.00 | $0.00 | $0.08-0.15 | $0.30-0.60 |
| **Efficiency** | Latency (p95) | <2s | <5s | <10s | <20s |
| **Efficiency** | Agent Count | 6 | 7 | 9 | 14 |
| **Efficiency** | Throughput (incidents/min) | ~30 | ~12 | ~6 | ~3 |
| **Safety** | Safety Violation Rate | -- | -- | -- | 0% |
| **Safety** | False Escalation Rate | -- | -- | -- | <10% |
| **Safety** | Human Override Rate | -- | -- | -- | 5-15% |
| **Coordination** | Debate Diversity (Jaccard) | -- | -- | -- | >0.3 |
| **Coordination** | Blackboard Utilization | -- | -- | >=70% | -- |
| **Learning** | Error-Repeat Reduction | -- | -- | -- | <5% after 50 |
| **Learning** | Self-Improvement Curve | -- | -- | -- | Monotonic increase |
| **Overfitting** | Train/Val Gap | <0.3 | <0.5 | -- (no fine-tuning) | -- (no fine-tuning) |
| **Statistical** | Friedman test (all differ?) | p < 0.05 across all 4 models | | | |
| **Statistical** | McNemar pairwise | p < 0.0083 (Bonferroni-corrected) per pair | | | |
| **Statistical** | Cohen's h (effect size) | >0.2 = meaningful | | | |
| **Qualitative** | SRE Correctness (1-5) | Rated | Rated | Rated | Rated |
| **Qualitative** | SRE Actionability (1-5) | Rated | Rated | Rated | Rated |
| **Qualitative** | SRE Trustworthiness (1-5) | Rated | Rated | Rated | Rated |

### How to Read This Framework

1. **"Which model is most accurate?"** -> Compare Unified RCA Accuracy row
2. **"Is the accuracy difference real?"** -> Check Friedman + McNemar p-values + Cohen's h
3. **"Is the extra cost of M3/M4 worth it?"** -> Plot cost-efficiency frontier; run per-class McNemar to see if M3/M4 fix the specific classes M1/M2 get wrong (rbac, other, dns)
4. **"Which failure types need escalation?"** -> Per-root-cause accuracy comparison table
5. **"Is M4's debate actually helping?"** -> Debate diversity >0.3 + accuracy gain +5-10% + quality-vs-majority divergence >10%
6. **"Is the system safe for production?"** -> Safety violation rate = 0%, false escalation <10%, human override 5-15%
7. **"Is the system getting better over time?"** -> Error-repeat reduction <5%, self-improvement curve monotonically increasing

---

## How All 4 Models Relate: The Escalation Chain

The models aren't alternatives -- they're an **escalation ladder**:

```
Incident arrives
    |
    v
M1 classifies (Mistral-7B, $0.00, <2s)
    |
    +-- Known simple failure? (80% of incidents) --> M2 generates diagnosis ($0.00, <5s)
    |
    +-- Ambiguous/complex --> M3 validates via blackboard ($0.08-0.15, <10s)
                                  |
                                  +-- Confidence >=70% --> Execute M3's RCA
                                  |
                                  +-- Confidence <70% --> M4 debate consensus ($0.30-0.60, <20s)
```

**Why this routing?** Cost optimization. 80%+ of K8s incidents are simple (OOM, image pull, missing config) and resolve at $0 with M1/M2. Only the hard cases (ambiguous dns<->connection, novel failures, multi-signal scheduling) escalate to the expensive API-based models. This keeps the average cost per incident well below $0.05 while achieving the highest accuracy on the incidents that need it.
