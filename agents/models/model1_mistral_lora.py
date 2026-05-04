"""
Model 1: Mistral-7B LoRA Fine-Tuning for RCA Classification

Fine-tunes Mistral-7B-Instruct with LoRA on agent1_structured.parquet.
The model learns to classify the root cause family of Kubernetes incidents
from structured observability signals (pod status, events, restart counts, etc.).

Input format (prompt):
    A structured summary of K8s incident signals → root_cause_family label

Usage:
    python -m agents.models.model1_mistral_lora                    # defaults
    python -m agents.models.model1_mistral_lora \
        --input data/processed/agent1_structured.parquet \
        --outdir agents/models/trained/model1_mistral \
        --epochs 3 --batch-size 4
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import torch
from datasets import Dataset
from peft import LoraConfig, TaskType, get_peft_model
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
)
from trl import SFTTrainer, SFTConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_INPUT = Path("data/processed/agent1_structured.parquet")
DEFAULT_OUTDIR = Path("agents/models/trained/model1_mistral")
MODEL_ID = "mistralai/Mistral-7B-Instruct-v0.3"
MAX_SEQ_LEN = 512


# ---------------------------------------------------------------------------
# Data formatting
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a Kubernetes Root Cause Analysis agent. Given structured "
    "observability signals from a Kubernetes incident, classify the root "
    "cause family. Respond with ONLY the root cause label."
)


def format_incident_prompt(row: Dict[str, Any]) -> str:
    """Format a single incident row into a structured prompt."""
    fields = [
        f"Namespace: {row.get('namespace', 'unknown')}",
        f"Workload Kind: {row.get('workload_kind', 'unknown')}",
        f"Pod Status: {row.get('pod_status', 'unknown')}",
        f"Waiting Reason: {row.get('waiting_reason', 'unknown')}",
        f"Error Message: {row.get('error_message', 'unknown')}",
        f"Event Type: {row.get('event_type', 'unknown')}",
        f"Event Reason: {row.get('event_reason', 'unknown')}",
        f"Event Message: {row.get('event_message', 'unknown')}",
        f"Restart Count: {row.get('restart_count', 0)}",
        f"OOM Killed: {row.get('oom_killed', False)}",
        f"Symptom Family: {row.get('symptom_family', 'unknown')}",
        f"Difficulty: {row.get('difficulty', 'unknown')}",
        f"Noise Level: {row.get('noise_level', 0)}",
    ]
    return "\n".join(fields)


def build_chat_text(row: Dict[str, Any]) -> str:
    """Build a full chat-formatted training example."""
    incident_text = format_incident_prompt(row)
    target = row.get("root_cause_family", "unknown")

    # Mistral instruct format
    text = (
        f"<s>[INST] {SYSTEM_PROMPT}\n\n"
        f"Classify the root cause of this Kubernetes incident:\n\n"
        f"{incident_text} [/INST] {target}</s>"
    )
    return text


def prepare_dataset(input_path: Path, test_size: float = 0.2) -> tuple:
    """Load parquet, format into chat texts, and split."""
    df = pd.read_parquet(input_path)
    print(f"[data] loaded {len(df):,} rows")

    # Fill NaN for string columns
    str_cols = df.select_dtypes(include=["object"]).columns
    df[str_cols] = df[str_cols].fillna("unknown")
    df = df.fillna(0)

    # Get label distribution
    label_counts = df["root_cause_family"].value_counts()
    print(f"[data] root_cause_family distribution:\n{label_counts}\n")

    # Build chat-formatted texts
    records = df.to_dict("records")
    texts = [build_chat_text(r) for r in records]
    df_text = pd.DataFrame({"text": texts})

    # Split
    dataset = Dataset.from_pandas(df_text)
    split = dataset.train_test_split(test_size=test_size, seed=42)
    print(f"[data] train: {len(split['train']):,}, test: {len(split['test']):,}")
    return split["train"], split["test"], list(label_counts.index)


# ---------------------------------------------------------------------------
# Model setup
# ---------------------------------------------------------------------------


def load_model_and_tokenizer(model_id: str) -> tuple:
    """Load Mistral with 4-bit quantization and LoRA config."""
    print(f"[model] loading {model_id}...")

    # Quantization config for memory efficiency
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id

    # Detect device
    if torch.cuda.is_available():
        device_map = "auto"
        print("[model] using CUDA GPU")
    elif torch.backends.mps.is_available():
        # MPS doesn't support bitsandbytes quantization — load in float16
        device_map = "mps"
        bnb_config = None
        print("[model] using Apple MPS (no quantization)")
    else:
        device_map = "cpu"
        bnb_config = None
        print("[model] using CPU (no quantization)")

    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        quantization_config=bnb_config,
        device_map=device_map,
        torch_dtype=torch.float16,
        trust_remote_code=True,
    )
    model.config.use_cache = False

    # LoRA config
    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )

    model = get_peft_model(model, lora_config)
    trainable, total = model.get_nb_trainable_parameters()
    print(f"[model] trainable params: {trainable:,} / {total:,} ({100*trainable/total:.2f}%)")

    return model, tokenizer, lora_config


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def train(
    model,
    tokenizer,
    train_dataset: Dataset,
    eval_dataset: Dataset,
    outdir: Path,
    epochs: int = 3,
    batch_size: int = 4,
    learning_rate: float = 2e-4,
) -> None:
    """Run SFT training with LoRA."""
    outdir.mkdir(parents=True, exist_ok=True)

    training_args = SFTConfig(
        output_dir=str(outdir),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        gradient_accumulation_steps=4,
        learning_rate=learning_rate,
        weight_decay=0.01,
        warmup_ratio=0.1,
        lr_scheduler_type="cosine",
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        fp16=torch.cuda.is_available(),
        report_to="none",
        max_seq_length=MAX_SEQ_LEN,
        dataset_text_field="text",
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        args=training_args,
    )

    print(f"\n[train] starting LoRA fine-tuning for {epochs} epochs...")
    trainer.train()

    # Save the LoRA adapter
    adapter_path = outdir / "lora_adapter"
    model.save_pretrained(str(adapter_path))
    tokenizer.save_pretrained(str(adapter_path))
    print(f"[save] LoRA adapter saved to {adapter_path}")

    # Save training metrics
    metrics = trainer.evaluate()
    metrics_path = outdir / "eval_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"[save] eval metrics saved to {metrics_path}")
    print(f"[eval] loss: {metrics.get('eval_loss', 'N/A'):.4f}")

    return trainer


# ---------------------------------------------------------------------------
# Inference helper
# ---------------------------------------------------------------------------


def predict(model, tokenizer, incident_row: Dict[str, Any]) -> str:
    """Run inference on a single incident to get predicted root cause."""
    incident_text = format_incident_prompt(incident_row)
    prompt = (
        f"<s>[INST] {SYSTEM_PROMPT}\n\n"
        f"Classify the root cause of this Kubernetes incident:\n\n"
        f"{incident_text} [/INST]"
    )

    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=MAX_SEQ_LEN)
    inputs = {k: v.to(model.device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=20,
            temperature=0.1,
            do_sample=False,
        )

    response = tokenizer.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    return response.strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(input_path: Path, outdir: Path, epochs: int = 3, batch_size: int = 4) -> None:
    """Full Model 1 Mistral LoRA training pipeline."""
    # 1. Prepare dataset
    train_ds, eval_ds, labels = prepare_dataset(input_path)

    # 2. Load model with LoRA
    model, tokenizer, lora_config = load_model_and_tokenizer(MODEL_ID)

    # 3. Train
    trainer = train(model, tokenizer, train_ds, eval_ds, outdir, epochs, batch_size)

    # 4. Save metadata
    metadata = {
        "model_id": MODEL_ID,
        "task": "rca_classification",
        "target": "root_cause_family",
        "labels": labels,
        "lora_r": lora_config.r,
        "lora_alpha": lora_config.lora_alpha,
        "epochs": epochs,
        "batch_size": batch_size,
        "max_seq_len": MAX_SEQ_LEN,
        "dataset_rows": len(train_ds) + len(eval_ds),
    }
    with open(outdir / "model1_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\n[done] Model 1 (Mistral LoRA) training complete.")
    print(f"  Adapter: {outdir}/lora_adapter/")
    print(f"  Metadata: {outdir}/model1_metadata.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Model 1: Mistral-7B LoRA for RCA classification")
    parser.add_argument("--input", type=str, default=str(DEFAULT_INPUT))
    parser.add_argument("--outdir", type=str, default=str(DEFAULT_OUTDIR))
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    args = parser.parse_args()
    run(Path(args.input), Path(args.outdir), args.epochs, args.batch_size)


if __name__ == "__main__":
    main()
