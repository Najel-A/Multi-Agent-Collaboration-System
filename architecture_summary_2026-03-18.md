# Model Training Architecture Summary

## Files Created

| File | Purpose |
|------|---------|
| `agents/models/model1_mistral_lora.py` | Model 1: Mistral-7B LoRA fine-tuning for RCA classification |
| `agents/models/model2_qwen_lora.py` | Model 2: Qwen2.5-7B LoRA fine-tuning for RCA generation |
| `train_models.ipynb` | Notebook to run both training pipelines + inference tests |
| `agents/__init__.py` | Package init |
| `agents/models/__init__.py` | Package init |

## How to Train

**Option A — Notebook** (recommended):
```
jupyter notebook train_models.ipynb
```

**Option B — CLI**:
```bash
# Model 1: Mistral — RCA classification
python -m agents.models.model1_mistral_lora \
    --input data/processed/agent1_structured.parquet \
    --outdir agents/models/trained/model1_mistral \
    --epochs 3 --batch-size 4

# Model 2: Qwen — RCA generation
python -m agents.models.model2_qwen_lora \
    --input data/processed/agent2_evidence.parquet \
    --outdir agents/models/trained/model2_qwen \
    --epochs 3 --batch-size 2
```

## Architecture Summary

- **Model 1 (Mistral-7B)**: Structured K8s signals → classifies `root_cause_family` (LoRA rank=16, target: q/k/v/o_proj, max_seq=512)
- **Model 2 (Qwen2.5-7B)**: Raw evidence text → generates diagnosis + fix plan + verification (LoRA rank=16, target: attention + MLP layers, max_seq=1024)
- Both auto-detect device (CUDA → 4-bit quantization, MPS → float16, CPU → float16)
- Trained adapters save to `agents/models/trained/` with metadata JSON
