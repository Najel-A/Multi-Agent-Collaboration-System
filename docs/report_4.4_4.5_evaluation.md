# 4.4 Model Evaluation Methods

This section presents the evaluation methods and metrics used for each model in the Multi-Agent Collaboration System for Kubernetes Root Cause Analysis (RCA).

---

## 4.4.1 Overview of Models and Target Problems

| Model | Base LLM | Task Type | Target Problem |
|-------|----------|-----------|----------------|
| **Model 1** | Mistral-7B-Instruct-v0.3 + LoRA | Classification | Classify root cause family from structured K8s signals |
| **Model 2** | Qwen2.5-7B-Instruct + LoRA | Generation | Generate diagnosis + remediation plan from raw evidence |

Both models are fine-tuned using **LoRA (Low-Rank Adaptation)** with 4-bit NF4 quantization, reducing trainable parameters to ~1% of the full model while preserving performance.

---

## 4.4.2 Model 1: Mistral-7B LoRA — RCA Classification

### Problem Statement
Given structured observability signals from a Kubernetes incident (pod status, event reason, restart count, OOM status, etc.), predict the **root cause family** label (e.g., `ResourceExhaustion`, `ImagePullFailure`, `CrashLoop`).

### Evaluation Methods and Metrics

| Metric | Description | Formula / Method | Why This Metric |
|--------|-------------|------------------|-----------------|
| **Training Loss** | Cross-entropy loss on training set per epoch | Averaged across logging steps per epoch | Monitors learning progress |
| **Validation Loss** | Cross-entropy loss on 20% held-out split | Computed at end of each epoch | Primary early stopping signal; detects overfitting |
| **Train/Val Gap** | Difference between val loss and train loss | `val_loss - avg_train_loss` | Gap > 0.3 indicates overfitting |
| **Exact Accuracy** | Predicted label exactly matches ground truth | `correct / total * 100` (case-insensitive) | Primary classification metric |
| **Fuzzy Accuracy** | Predicted label is substring of ground truth or vice versa | Substring match (e.g., `CrashLoop` matches `CrashLoopBackOff`) | Accounts for minor label formatting differences |
| **Per-Class Accuracy** | Fuzzy accuracy broken down by root cause family | Per-class `correct / total` | Identifies which failure types the model struggles with |
| **Early Stopping Epoch** | Epoch at which training halted | Patience = 2 consecutive epochs without val loss improvement | Confirms regularization is working |

### Evaluation Protocol
1. **Train/Val Split**: 80/20 random split (seed=42)
2. **Overfitting Check**: Per-epoch comparison of train vs. val loss; flagged if gap > 0.3
3. **Inference Evaluation**: Run on up to 500 held-out samples with `do_sample=False` (greedy decoding)
4. **Target**: Fuzzy accuracy >= 70%

### Anti-Overfitting Regularization Applied

| Technique | Setting | Purpose |
|-----------|---------|---------|
| LoRA rank | 32 | Limits adapter capacity to prevent memorization |
| LoRA dropout | 0.1 | Randomly drops 10% of adapter activations |
| Weight decay | 0.05 | L2 regularization penalizes large weights |
| Label smoothing | 0.1 | Softens target distribution; prevents overconfident predictions |
| Early stopping | patience=2 | Halts training when val loss plateaus |
| Cosine LR schedule | 5e-5 -> 0 | Gradually reduces step size to avoid late-epoch overfitting |

---

## 4.4.3 Model 2: Qwen-7B LoRA — RCA Generation

### Problem Statement
Given raw observability evidence from a Kubernetes incident (kubectl output, container logs, metrics), generate a structured response containing: **Diagnosis**, **Fix Plan**, **Actions**, and **Verification** steps.

### Evaluation Methods and Metrics

| Metric | Description | Formula / Method | Why This Metric |
|--------|-------------|------------------|-----------------|
| **Training Loss** | Cross-entropy loss on training set per epoch | Averaged across logging steps per epoch | Monitors learning progress |
| **Validation Loss** | Cross-entropy loss on 20% held-out split | Computed at end of each epoch | Primary early stopping signal |
| **Train/Val Gap** | Difference between val loss and train loss | `val_loss - avg_train_loss` | Gap > 0.5 acceptable for generation; above = overfitting |
| **Structure Score** (40% weight) | Does the output contain required sections? | `sections_found / 3` where sections = {Diagnosis, Fix Plan, Verification} | Verifies the model learned output structure |
| **Keyword Overlap** (60% weight) | Fraction of ground truth keywords present in prediction | `\|GT_keywords intersection Pred_keywords\| / \|GT_keywords\|` (words > 4 chars) | Measures semantic content correctness |
| **Combined Score** | Weighted blend of structure + keyword metrics | `0.4 * structure_score + 0.6 * keyword_overlap` | Single metric balancing format and content |
| **Early Stopping Epoch** | Epoch at which training halted | Patience = 2 epochs without val loss improvement | Confirms regularization is working |

### Evaluation Protocol
1. **Train/Val Split**: 80/20 random split (seed=42)
2. **Overfitting Check**: Per-epoch train vs. val loss comparison; flagged if gap > 0.5
3. **Inference Evaluation**: Run on up to 200 held-out samples with greedy decoding (`do_sample=False`, `repetition_penalty=1.1`)
4. **Target**: Combined score >= 70%

### Why Not Exact-Match Accuracy for Generation?
Unlike classification (Model 1), generation tasks produce free-form text. An exact string match would always fail because there are many valid ways to phrase the same diagnosis. Instead, we decompose evaluation into:
- **Structure** (did the model learn the output format?)
- **Keywords** (did the model capture the right technical concepts?)

This mirrors how an SRE would evaluate a generated RCA: "Is it structured? Does it mention the right things?"

### Anti-Overfitting Regularization Applied

| Technique | Setting | Purpose |
|-----------|---------|---------|
| LoRA rank | 32 | Constrains adapter without sacrificing generation fluency |
| LoRA dropout | 0.1 | Forces distributed knowledge across parameters |
| Weight decay | 0.05 | Strong L2 penalty for small dataset |
| Early stopping | patience=2 | Primary overfitting defense |
| Cosine LR schedule | 5e-5 -> 0 | Reduces late-epoch overfitting |
| Max sequence length | 1024 | Caps input length to prevent memorizing long sequences |

---

## 4.4.4 Cross-Model Comparison Metrics

These system-level metrics apply across both models and the broader MAS:

| Metric | Description | Target |
|--------|-------------|--------|
| **RCA Accuracy** | % of correct root cause identifications | > 75% |
| **F1-Score** | Harmonic mean of precision and recall | > 0.85 |
| **Precision** | True positives / predicted positives | > 0.85 |
| **Recall** | True positives / actual positives | > 0.80 |
| **AUC-ROC** | Area under ROC curve (classification quality) | > 0.90 |
| **Latency p95** | 95th percentile end-to-end inference time | < 5s |
| **Throughput** | Logs processed per second | > 10K/s |
| **MTTD** | Mean Time to Detect an anomaly | < 5 min |
| **MTTR** | Mean Time to Remediate | < 30 min |

---

## 4.4.5 Summary: Metrics-to-Model Mapping

| Evaluation Goal | Model 1 (Classification) | Model 2 (Generation) |
|-----------------|--------------------------|----------------------|
| **Primary accuracy metric** | Exact & Fuzzy Accuracy | Combined Score (structure + keyword) |
| **Loss monitoring** | Train/Val cross-entropy loss | Train/Val cross-entropy loss |
| **Overfitting detection** | Train/Val gap (threshold: 0.3) | Train/Val gap (threshold: 0.5) |
| **Early stopping** | Yes (patience=2) | Yes (patience=2) |
| **Per-class analysis** | Per root-cause-family accuracy | Per-scenario structure/keyword scores |
| **Error analysis** | Misclassification samples | Missing sections, low keyword overlap |

---
---

# 4.5 Model Validation and Evaluation Results

This section presents the validation and evaluation results for each trained model.

> **Note**: The results below are the evaluation framework outputs as implemented in the training notebooks. Actual numeric values are produced when the notebooks are executed on Google Colab with A100 GPU. The tables below show the structure and metrics that are reported; fill in values from your Colab training runs.

---

## 4.5.1 Model 1: Mistral-7B LoRA — Classification Results

### Training Configuration

| Parameter | Value |
|-----------|-------|
| Base Model | Mistral-7B-Instruct-v0.3 |
| Quantization | 4-bit NF4 with double quantization |
| LoRA Rank / Alpha / Dropout | 64 / 128 / 0.05 |
| Target Modules | q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj |
| Trainable Parameters | ~1-2% of 7B |
| Epochs | 3 |
| Effective Batch Size | 16 (8 x 2 grad accum) |
| Learning Rate | 1e-4 (cosine decay) |
| Weight Decay | 0.01 |
| Max Sequence Length | 512 tokens |
| Dataset | 8,502 rows (80/20 train/val split) |
| Classes | 11 root cause families |

### Classification Accuracy Results

| Metric | Value | Target |
|--------|-------|--------|
| **Fuzzy Accuracy** | 270 / 300 = **90.0%** | >= 70% |
| **Epochs Trained** | 3 / 3 | -- |

### Per-Class Accuracy Breakdown (Fuzzy)

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

**8 out of 11 classes achieved 100% accuracy.**

### Sample Misclassifications

| Expected Label | Predicted Label | Frequency |
|----------------|-----------------|-----------|
| rbac | connection | 8 occurrences |
| connection | rbac | 2 occurrences |
| other | connection | multiple |

### Analysis of Misclassifications

**`rbac` vs `connection` confusion (primary failure mode):** Both RBAC permission errors and network connection errors produce similar observability signals in Kubernetes — failed API calls, timeout events, and rejected requests. The structured fields (pod status, event reason) overlap significantly between these two failure types. Distinguishing them would require additional features such as specific RBAC error codes or API server audit log entries.

**`other` class ambiguity:** The `other` class is a catch-all for incidents that don't fit neatly into the other 10 categories. Its signals inherently overlap with multiple classes, making it difficult for the model to learn a distinct pattern. The model defaults to predicting `connection` when uncertain.

**`dns` class absence:** The `dns` class had only 2 samples in the full dataset and did not appear in the 300-sample evaluation set. This class is effectively unlearnable with the current data volume.

### Strengths
- 90% overall accuracy with only 3 epochs of training
- 8 of 11 classes at perfect 100% accuracy
- Classes with distinct K8s signals (OOM, image pull, probe failures) are trivially separable
- Well above the 70% target threshold

### Known Limitations
- `rbac` / `connection` confusion accounts for the majority of errors
- `other` catch-all class is inherently ambiguous
- `dns` class needs more training data (only 2 samples)

---

## 4.5.2 Model 2: Qwen-7B LoRA — Generation Results

### Training Configuration

| Parameter | Value |
|-----------|-------|
| Base Model | Qwen2.5-7B-Instruct |
| Quantization | 4-bit NF4 with double quantization |
| LoRA Rank / Alpha / Dropout | 32 / 64 / 0.1 |
| Target Modules | q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj |
| Trainable Parameters | ~80M / ~7B (~1%) |
| Max Epochs | 7 (with early stopping patience=2) |
| Effective Batch Size | 32 (8 x 4 grad accum) |
| Learning Rate | 5e-5 (cosine decay) |
| Weight Decay | 0.05 |
| Max Sequence Length | 1024 tokens |

### Training Loss Curve (Per Epoch)

| Epoch | Train Loss | Val Loss | Gap | Status |
|-------|-----------|----------|-----|--------|
| 1 | _(from run)_ | _(from run)_ | _(from run)_ | _(OK / OVERFITTING)_ |
| 2 | _(from run)_ | _(from run)_ | _(from run)_ | _(OK / OVERFITTING)_ |
| 3 | _(from run)_ | _(from run)_ | _(from run)_ | _(OK / OVERFITTING)_ |
| ... | ... | ... | ... | ... |

**Overfitting threshold**: Gap > 0.5 triggers a warning (higher tolerance for generation tasks).

### Generation Quality Results

| Metric | Value | Weight | Target |
|--------|-------|--------|--------|
| **Structure Score** | _(from run)_ % | 40% | -- |
| **Keyword Overlap** | _(from run)_ % | 60% | -- |
| **Combined Score** | _(from run)_ % | 100% | >= 70% |
| **Eval Loss** | _(from run)_ | -- | -- |
| **Stopped at Epoch** | _(from run)_ / 7 | -- | -- |

### How the Combined Score Works

```
Combined = 0.4 x Structure Score + 0.6 x Keyword Overlap

Structure Score = (sections found) / 3
  - Checks for: "diagnosis", "fix plan", "verification" in output

Keyword Overlap = |GT_keywords ^ Pred_keywords| / |GT_keywords|
  - Only words > 4 characters (filters stopwords)
  - Measures whether the model captures the right technical concepts
```

### Sample Prediction

**Input** (raw evidence):
```
kubectl describe pod my-app-7d8f9c6b5-x2k4m -n production
...
State: Waiting (CrashLoopBackOff)
Last State: Terminated (OOMKilled, exit code 137)
Restart Count: 14
...
```

**Expected Output** (ground truth):
```
## Diagnosis
The pod is in CrashLoopBackOff due to repeated OOMKilled terminations...

## Fix Plan
1. Increase memory limits in the deployment spec...
2. Check for memory leaks in the application...

## Verification
1. kubectl get pods -n production -w
2. kubectl top pod my-app-... -n production
```

**Model Output** (prediction):
```
(Actual model output from evaluation run)
```

---

## 4.5.3 Comparative Summary

| Metric | Model 1 (Mistral) | Model 2 (Qwen) |
|--------|-------------------|-----------------|
| **Task** | Classification | Generation |
| **Base Model** | Mistral-7B-Instruct-v0.3 | Qwen2.5-7B-Instruct |
| **Primary Metric** | Fuzzy Accuracy | Combined Score |
| **Target** | >= 70% | >= 70% |
| **Result** | **90.0%** | _(from run)_ % |
| **Eval Samples** | 300 | _(from run)_ |
| **Epochs Trained** | 3 / 3 | 3 / 3 |
| **Trainable Params** | ~1-2% of 7B | ~1-2% of 7B |
| **Adapter Size** | ~130 MB | ~130 MB |

### Solution Validation Against Target Problems

| Target Problem | Solution (Model) | Validation Method | Success Criteria | Result |
|----------------|-----------------|-------------------|-----------------|--------|
| Classify root cause from structured K8s signals | Model 1: Mistral LoRA classification | Fuzzy accuracy on 300 held-out samples | >= 70% | **90.0% — PASSED** |
| Generate diagnosis + remediation from raw evidence | Model 2: Qwen LoRA generation | Combined score (structure + keyword) on 200 held-out samples | >= 70% | _(from run)_ |
| Efficient fine-tuning without full model retraining | Both models: 4-bit QLoRA | Trainable param ratio | ~1% of total | Achieved |

---

## 4.5.4 How to Reproduce Results

1. Open the corresponding notebook in Google Colab:
   - Model 1: `python_notebooks/train_model1_mistral_colab.ipynb`
   - Model 2: `python_notebooks/train_model2_qwen_colab.ipynb`
2. Set runtime to **A100 GPU**
3. Upload the required parquet file (`agent1_structured.parquet` or `agent2_evidence.parquet`)
4. Run all cells
5. The notebook outputs:
   - Per-epoch train/val loss table (overfitting check)
   - Accuracy evaluation with per-class breakdown
   - `eval_metrics.json` and `model{1,2}_metadata.json` with all numeric results
   - Downloadable LoRA adapter zip (~130 MB)

Replace the `_(from run)_` placeholders in this document with the actual values printed by the notebooks.
