from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from datasets import Dataset


@dataclass(frozen=True)
class GeneratorConfig:
    """
    Scaffold config for generative training.
    Keep it model-agnostic so you can plug in Qwen/Mistral/others later.
    """

    model_name: str = "Qwen/Qwen2.5-7B-Instruct"
    dataset_text_field: str = "text"  # for SFTTrainer-style datasets
    max_length: int = 1024
    output_dir: Path = Path("najel-data/training/artifacts/generator")

    # Training knobs (defaults are placeholders; tune per environment)
    num_train_epochs: int = 1
    per_device_train_batch_size: int = 1
    per_device_eval_batch_size: int = 1
    gradient_accumulation_steps: int = 4
    learning_rate: float = 1e-4

    # LoRA ready (optional)
    use_lora: bool = True
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05


def build_sft_dataset(df: pd.DataFrame, *, text_col: str = "text") -> Dataset:
    if text_col not in df.columns:
        raise KeyError(f"Missing {text_col!r} column. Build generation dataset with use_chat_format=True.")
    texts = df[text_col].fillna("").astype(str).tolist()
    return Dataset.from_dict({text_col: texts})


def train_generator_sft(
    df_train: pd.DataFrame,
    df_eval: Optional[pd.DataFrame],
    *,
    cfg: GeneratorConfig,
) -> dict[str, Any]:
    """
    Training scaffold using TRL SFTTrainer if available.
    If TRL/bitsandbytes/peft aren't installed, this raises with a clear message.
    """
    try:
        import torch
        from peft import LoraConfig, TaskType, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from trl import SFTConfig, SFTTrainer
    except Exception as exc:
        raise RuntimeError(
            "Missing generative training dependencies. Install: peft trl transformers torch bitsandbytes"
        ) from exc

    outdir = Path(cfg.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(cfg.model_name, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Default: 4-bit for practicality (can be swapped later)
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    model = AutoModelForCausalLM.from_pretrained(
        cfg.model_name,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
    )

    if cfg.use_lora:
        lora_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=cfg.lora_r,
            lora_alpha=cfg.lora_alpha,
            lora_dropout=cfg.lora_dropout,
            bias="none",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        )
        model = get_peft_model(model, lora_config)

    train_ds = build_sft_dataset(df_train, text_col=cfg.dataset_text_field)
    eval_ds = None
    if df_eval is not None:
        eval_ds = build_sft_dataset(df_eval, text_col=cfg.dataset_text_field)

    args = SFTConfig(
        output_dir=str(outdir),
        num_train_epochs=cfg.num_train_epochs,
        per_device_train_batch_size=cfg.per_device_train_batch_size,
        per_device_eval_batch_size=cfg.per_device_eval_batch_size,
        gradient_accumulation_steps=cfg.gradient_accumulation_steps,
        learning_rate=cfg.learning_rate,
        logging_steps=20,
        eval_strategy="steps" if eval_ds is not None else "no",
        eval_steps=200 if eval_ds is not None else None,
        save_strategy="steps",
        save_steps=200,
        save_total_limit=2,
        report_to="none",
        max_length=cfg.max_length,
        dataset_text_field=cfg.dataset_text_field,
    )

    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        args=args,
    )
    trainer.train()

    trainer.save_model(str(outdir / "model"))
    tokenizer.save_pretrained(str(outdir / "tokenizer"))
    (outdir / "config.json").write_text(
        json.dumps({**cfg.__dict__, "output_dir": str(cfg.output_dir)}, indent=2, default=str),
        encoding="utf-8",
    )

    return {"output_dir": str(outdir)}

