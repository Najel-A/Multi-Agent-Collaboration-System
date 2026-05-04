from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class SplitConfig:
    test_size: float = 0.2
    val_size: float = 0.1
    seed: int = 42
    stratify_col: Optional[str] = "scenario_id"


@dataclass(frozen=True)
class DataConfig:
    jsonl_folder: Path
    jsonl_glob: str = "*.jsonl"
    schema_name: str = "k8s_incident_v1"


@dataclass(frozen=True)
class ClassificationConfig:
    task_name: str = "scenario_classification"
    model_name: str = "distilbert-base-uncased"
    text_col: str = "prompt"
    label_col: str = "label"
    max_length: int = 512
    output_dir: Path = Path("najel-data/training/artifacts/classifier")
    num_train_epochs: int = 3
    train_batch_size: int = 8
    eval_batch_size: int = 8
    learning_rate: float = 5e-5
    weight_decay: float = 0.01

