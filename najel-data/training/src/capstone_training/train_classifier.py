from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
from datasets import Dataset
from sklearn.model_selection import train_test_split
from collections import Counter
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    DataCollatorWithPadding,
    EvalPrediction,
    Trainer,
    TrainingArguments,
)

from .config import ClassificationConfig, SplitConfig
from .eval_utils import compute_classification_metrics, topk_accuracy


def _encode_labels(labels: list[str]) -> tuple[list[int], dict[str, int], dict[int, str]]:
    uniq = sorted(set(labels))
    label2id = {l: i for i, l in enumerate(uniq)}
    id2label = {i: l for l, i in label2id.items()}
    y = [label2id[str(l)] for l in labels]
    return y, label2id, id2label


def _hf_compute_metrics(id2label: dict[int, str]):
    def compute(eval_pred: EvalPrediction):
        logits = eval_pred.predictions
        y_true = eval_pred.label_ids
        y_pred = logits.argmax(axis=1)
        metrics = compute_classification_metrics(
            y_true.tolist(), y_pred.tolist(), labels=[id2label[i] for i in sorted(id2label)]
        )
        out = {
            "accuracy": metrics.accuracy,
            "precision_macro": metrics.precision_macro,
            "recall_macro": metrics.recall_macro,
            "f1_macro": metrics.f1_macro,
            "top3_accuracy": topk_accuracy(np.asarray(logits), np.asarray(y_true), k=3),
        }
        return out

    return compute


def train_classifier_from_dataframe(
    df: pd.DataFrame,
    *,
    cfg: ClassificationConfig,
    split: SplitConfig = SplitConfig(),
) -> dict[str, Any]:
    """
    Config-driven baseline classification pipeline.

    Input columns expected:
    - cfg.text_col: prompt/text
    - cfg.label_col: label string (scenario_id)
    """
    text = df[cfg.text_col].fillna("").astype(str).tolist()
    labels = df[cfg.label_col].fillna("").astype(str).tolist()
    y, label2id, id2label = _encode_labels(labels)

    stratify = y if (split.stratify_col is not None) else None
    if stratify is not None:
        counts = Counter(stratify)
        min_count = min(counts.values()) if counts else 0
        if min_count < 2:
            # Can't stratify if any class has <2 samples.
            stratify = None

    try:
        X_train, X_tmp, y_train, y_tmp = train_test_split(
            text, y, test_size=split.test_size, random_state=split.seed, stratify=stratify
        )
    except ValueError:
        # Fall back to non-stratified if dataset is too imbalanced for requested split.
        X_train, X_tmp, y_train, y_tmp = train_test_split(
            text, y, test_size=split.test_size, random_state=split.seed, stratify=None
        )
    # val split from tmp
    val_size_rel = split.val_size / max(split.test_size, 1e-9)
    try:
        X_val, X_test, y_val, y_test = train_test_split(
            X_tmp,
            y_tmp,
            test_size=(1.0 - val_size_rel),
            random_state=split.seed,
            stratify=y_tmp if stratify is not None else None,
        )
    except ValueError:
        X_val, X_test, y_val, y_test = train_test_split(
            X_tmp,
            y_tmp,
            test_size=(1.0 - val_size_rel),
            random_state=split.seed,
            stratify=None,
        )

    tokenizer = AutoTokenizer.from_pretrained(cfg.model_name, use_fast=True)

    def tok(batch):
        return tokenizer(batch["text"], truncation=True, max_length=cfg.max_length)

    ds_train = Dataset.from_dict({"text": X_train, "labels": y_train}).map(tok, batched=True)
    ds_val = Dataset.from_dict({"text": X_val, "labels": y_val}).map(tok, batched=True)
    ds_test = Dataset.from_dict({"text": X_test, "labels": y_test}).map(tok, batched=True)

    model = AutoModelForSequenceClassification.from_pretrained(
        cfg.model_name,
        num_labels=len(label2id),
        id2label={i: l for i, l in id2label.items()},
        label2id={l: i for l, i in label2id.items()},
    )

    outdir = Path(cfg.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    args = TrainingArguments(
        output_dir=str(outdir),
        num_train_epochs=cfg.num_train_epochs,
        per_device_train_batch_size=cfg.train_batch_size,
        per_device_eval_batch_size=cfg.eval_batch_size,
        learning_rate=cfg.learning_rate,
        weight_decay=cfg.weight_decay,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="accuracy",
        report_to="none",
        logging_steps=25,
    )

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=ds_train,
        eval_dataset=ds_val,
        processing_class=tokenizer,
        data_collator=DataCollatorWithPadding(tokenizer=tokenizer),
        compute_metrics=_hf_compute_metrics(id2label),
    )

    trainer.train()

    # Evaluate on test set
    preds = trainer.predict(ds_test)
    logits = preds.predictions
    y_true = np.asarray(y_test)
    y_pred = logits.argmax(axis=1)

    metrics = compute_classification_metrics(
        y_true.tolist(),
        y_pred.tolist(),
        labels=[id2label[i] for i in sorted(id2label)],
    )

    # Save artifacts
    trainer.save_model(str(outdir / "model"))
    tokenizer.save_pretrained(str(outdir / "tokenizer"))
    (outdir / "label2id.json").write_text(json.dumps(label2id, indent=2), encoding="utf-8")
    (outdir / "config.json").write_text(json.dumps(asdict(cfg), indent=2, default=str), encoding="utf-8")
    (outdir / "metrics_test.json").write_text(
        json.dumps(
            {
                "accuracy": metrics.accuracy,
                "precision_macro": metrics.precision_macro,
                "recall_macro": metrics.recall_macro,
                "f1_macro": metrics.f1_macro,
                "confusion_matrix": metrics.confusion_matrix,
                "labels": metrics.labels,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "output_dir": str(outdir),
        "label2id": label2id,
        "metrics_test": metrics,
    }

