"""
Model 2: Qwen LoRA Fine-Tuning for Text-Based RCA

Fine-tunes Qwen2.5-7B-Instruct with LoRA on agent2_evidence.parquet.
The model learns to generate root cause diagnosis and remediation plans
from raw Kubernetes observability evidence (logs, events, describe output).

Input format (prompt):
    Raw evidence text (kubectl output, logs, metrics) → diagnosis + fix plan

Usage:
    python -m agents.models.model2_qwen_lora                    # defaults
    python -m agents.models.model2_qwen_lora \
        --input data/processed/agent2_evidence.parquet \
        --outdir agents/models/trained/model2_qwen \
        --epochs 3 --batch-size 2
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

DEFAULT_INPUT = Path("data/processed/agent2_evidence.parquet")
DEFAULT_OUTDIR = Path("agents/models/trained/model2_qwen")
MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"
MAX_SEQ_LEN = 1024


# ---------------------------------------------------------------------------
# Data formatting
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a Kubernetes Site Reliability Engineering (SRE) agent. "
    "Given raw observability evidence from a Kubernetes incident — including "
    "kubectl output, container logs, and metrics — provide:\n"
    "1. A root cause diagnosis explaining what went wrong and why.\n"
    "2. A step-by-step fix plan to resolve the incident.\n"
    "3. Verification steps to confirm the fix worked."
)


def build_evidence_prompt(row: Dict[str, Any]) -> str:
    """Format evidence fields into the user prompt."""
    parts = []

    evidence = row.get("evidence_text", "")
    if evidence:
        parts.append(evidence)

    scenario = row.get("scenario_id", "")
    if scenario:
        parts.append(f"\nScenario: {scenario}")

    difficulty = row.get("difficulty", "")
    if difficulty:
        parts.append(f"Difficulty: {difficulty}")

    return "\n".join(parts)


def build_target_response(row: Dict[str, Any]) -> str:
    """Build the expected model response from remediation fields."""
    sections = []

    diagnosis = row.get("diagnosis_text", "")
    if diagnosis:
        sections.append(f"## Diagnosis\n{diagnosis}")

    fix_plan = row.get("fix_plan_text", "")
    if fix_plan:
        sections.append(f"## Fix Plan\n{fix_plan}")

    actions = row.get("actions_text", "")
    if actions:
        sections.append(f"## Actions\n{actions}")

    verification = row.get("verification_text", "")
    if verification:
        sections.append(f"## Verification\n{verification}")

    rollback = row.get("rollback_text", "")
    if rollback:
        sections.append(f"## Rollback\n{rollback}")

    return "\n\n".join(sections) if sections else "No remediation available."


def build_chat_text(row: Dict[str, Any]) -> str:
    """Build a Qwen ChatML-formatted training example."""
    evidence = build_evidence_prompt(row)
    target = build_target_response(row)

    # Qwen uses ChatML format
    text = (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\n"
        f"Analyze this Kubernetes incident and provide diagnosis and remediation:\n\n"
        f"{evidence}<|im_end|>\n"
        f"<|im_start|>assistant\n{target}<|im_end|>"
    )
    return text


def prepare_dataset(input_path: Path, test_size: float = 0.2) -> tuple:
    """Load parquet, format into chat texts, and split."""
    df = pd.read_parquet(input_path)
    print(f"[data] loaded {len(df):,} rows")

    # Fill NaN
    str_cols = df.select_dtypes(include=["object"]).columns
    df[str_cols] = df[str_cols].fillna("")
    df = df.fillna(0)

    # Show scenario distribution
    label_counts = df["scenario_id"].value_counts()
    print(f"[data] scenario_id distribution:\n{label_counts.head(10)}\n... ({len(label_counts)} unique scenarios)")

    # Build chat-formatted texts
    records = df.to_dict("records")
    texts = [build_chat_text(r) for r in records]

    # Report text length stats
    lengths = [len(t) for t in texts]
    print(f"[data] text length — min: {min(lengths)}, max: {max(lengths)}, "
          f"mean: {sum(lengths)/len(lengths):.0f}")

    df_text = pd.DataFrame({"text": texts})
    dataset = Dataset.from_pandas(df_text)
    split = dataset.train_test_split(test_size=test_size, seed=42)
    print(f"[data] train: {len(split['train']):,}, test: {len(split['test']):,}")
    return split["train"], split["test"], list(label_counts.index)


# ---------------------------------------------------------------------------
# Model setup
# ---------------------------------------------------------------------------


def load_model_and_tokenizer(model_id: str) -> tuple:
    """Load Qwen with 4-bit quantization and LoRA config."""
    print(f"[model] loading {model_id}...")

    # Quantization config
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

    # LoRA config — target Qwen's attention layers
    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        task_type=TaskType.CAUSAL_LM,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
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
    batch_size: int = 2,
    learning_rate: float = 2e-4,
) -> None:
    """Run SFT training with LoRA."""
    outdir.mkdir(parents=True, exist_ok=True)

    training_args = SFTConfig(
        output_dir=str(outdir),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        gradient_accumulation_steps=8,
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


def predict(model, tokenizer, evidence_text: str, scenario_id: str = "", difficulty: str = "") -> str:
    """Run inference on raw evidence text to generate diagnosis + fix plan."""
    row = {
        "evidence_text": evidence_text,
        "scenario_id": scenario_id,
        "difficulty": difficulty,
    }
    evidence = build_evidence_prompt(row)

    prompt = (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\n"
        f"Analyze this Kubernetes incident and provide diagnosis and remediation:\n\n"
        f"{evidence}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )

    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=MAX_SEQ_LEN)
    inputs = {k: v.to(model.device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=512,
            temperature=0.3,
            do_sample=True,
            top_p=0.9,
            repetition_penalty=1.1,
        )

    response = tokenizer.decode(outputs[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    # Strip any trailing ChatML tokens
    if "<|im_end|>" in response:
        response = response.split("<|im_end|>")[0]
    return response.strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(input_path: Path, outdir: Path, epochs: int = 3, batch_size: int = 2) -> None:
    """Full Model 2 Qwen LoRA training pipeline."""
    # 1. Prepare dataset
    train_ds, eval_ds, scenarios = prepare_dataset(input_path)

    # 2. Load model with LoRA
    model, tokenizer, lora_config = load_model_and_tokenizer(MODEL_ID)

    # 3. Train
    trainer = train(model, tokenizer, train_ds, eval_ds, outdir, epochs, batch_size)

    # 4. Save metadata
    metadata = {
        "model_id": MODEL_ID,
        "task": "rca_generation",
        "input": "evidence_text (kubectl output, logs, metrics)",
        "output": "diagnosis + fix_plan + actions + verification",
        "scenarios": scenarios,
        "lora_r": lora_config.r,
        "lora_alpha": lora_config.lora_alpha,
        "lora_target_modules": list(lora_config.target_modules),
        "epochs": epochs,
        "batch_size": batch_size,
        "max_seq_len": MAX_SEQ_LEN,
        "dataset_rows": len(train_ds) + len(eval_ds),
    }
    with open(outdir / "model2_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\n[done] Model 2 (Qwen LoRA) training complete.")
    print(f"  Adapter: {outdir}/lora_adapter/")
    print(f"  Metadata: {outdir}/model2_metadata.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Model 2: Qwen LoRA for text-based RCA")
    parser.add_argument("--input", type=str, default=str(DEFAULT_INPUT))
    parser.add_argument("--outdir", type=str, default=str(DEFAULT_OUTDIR))
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--lr", type=float, default=2e-4)
    args = parser.parse_args()
    run(Path(args.input), Path(args.outdir), args.epochs, args.batch_size)


if __name__ == "__main__":
    main()
