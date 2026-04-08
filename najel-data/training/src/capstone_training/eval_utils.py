from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    precision_recall_fscore_support,
)


@dataclass(frozen=True)
class ClassificationMetrics:
    accuracy: float
    precision_macro: float
    recall_macro: float
    f1_macro: float
    report: Dict[str, Any]
    confusion_matrix: List[List[int]]
    labels: List[str]


def compute_classification_metrics(
    y_true: list[int],
    y_pred: list[int],
    *,
    labels: Optional[list[str]] = None,
) -> ClassificationMetrics:
    acc = float(accuracy_score(y_true, y_pred))
    p, r, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, average="macro", zero_division=0
    )
    cm = confusion_matrix(y_true, y_pred).tolist()
    rep = classification_report(y_true, y_pred, output_dict=True, zero_division=0)
    return ClassificationMetrics(
        accuracy=acc,
        precision_macro=float(p),
        recall_macro=float(r),
        f1_macro=float(f1),
        report=rep,
        confusion_matrix=cm,
        labels=labels or [],
    )


def topk_accuracy(logits: np.ndarray, y_true: np.ndarray, k: int = 3) -> float:
    topk = np.argsort(-logits, axis=1)[:, :k]
    hits = (topk == y_true.reshape(-1, 1)).any(axis=1)
    return float(hits.mean())

