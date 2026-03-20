# Multi-Agent System — Model Training Outline

## Current State

- **Data is ready**: `agent1_structured.parquet` (8,502 rows, tabular features) and `agent2_evidence.parquet` (text/evidence data)
- **6 model architectures defined** in `multi_agent_models.md`
- **No training code exists yet** — `agents/models/configs/` is empty

---

## Step-by-Step Training Plan

### Step 1 — Model 1: Classical ML Baseline (Agent 1 data)

Train anomaly detection + classification models on `agent1_structured.parquet`:

1. **Load** the parquet file, inspect features (`pod_status`, `restart_count`, `event_type`, `symptom_family`, `root_cause_family`, etc.)
2. **Encode** categorical features (LabelEncoder or OneHotEncoder)
3. **Train/test split** (80/20, stratified by `scenario_id`)
4. **Train classifiers**: RandomForest, XGBoost, LightGBM for RCA classification (predict `root_cause_family` or `scenario_id`)
5. **Train anomaly detectors**: Isolation Forest, One-Class SVM, LOF
6. **Evaluate**: F1, Precision, Recall, confusion matrix
7. **Save** models to `agents/models/`

### Step 2 — Model 2: Deep Sequence / Transformer (Agent 2 data)

Train on `agent2_evidence.parquet` text fields:

1. **Generate embeddings** from `evidence_text` using a pretrained model (e.g., `sentence-transformers/all-MiniLM-L6-v2`)
2. **Build FAISS index** for RAG retrieval
3. **Fine-tune LogBERT** or train an LSTM/GRU on log sequences for anomaly detection
4. **Train a text classifier** on embeddings → `scenario_id`
5. **Evaluate** retrieval accuracy (top-k recall) and classification metrics

### Step 3 — Agent Integration

1. Wire trained models into agent classes (Anomaly Detection Agent, RCA Agent, Retrieval Agent)
2. Build the Planner → Executor → Reviewer pipeline (Model 1 architecture)
3. Add LLM calls (GPT-4o-mini / Claude Haiku) for natural language reasoning

### Step 4 — Advanced Models (3–6)

Build incrementally on top of Models 1 & 2:

- **Model 3**: Add SMART decomposition + tool grounding
- **Model 4**: Implement Contract Net Protocol bidding
- **Model 5**: Federated learning across simulated regions
- **Model 6**: Multi-agent debate with heterogeneous LLMs

### Step 5 — Evaluation

Compare all models using metrics from `evaluation-metrics.md`: F1, RCA Accuracy, MTTD, MTTR
