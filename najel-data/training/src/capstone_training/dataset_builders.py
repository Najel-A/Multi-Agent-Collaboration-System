from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

import pandas as pd

from .prompt_builders import (
    ClassificationPromptConfig,
    RemediationPromptConfig,
    build_classification_prompt,
    build_generation_prompt,
    build_chatml_text,
    format_remediation_target,
)


BuilderFn = Callable[[pd.DataFrame, "TaskSpec"], pd.DataFrame]


@dataclass(frozen=True)
class TaskSpec:
    """
    Task definition for building a task-specific dataset.
    The intent is to keep this future-proof: add fields as needs evolve.
    """

    name: str
    task_type: str  # "classification" | "generation" | future types
    input_col: str = "evidence_text"
    target_col: str = "scenario_id"
    text_col_out: str = "text"
    prompt_col_out: str = "prompt"
    label_col_out: str = "label"
    use_chat_format: bool = False

    # Prompt/target builder configs are stored as dict to keep JSON-serializable configs possible later.
    prompt_cfg: Optional[dict[str, Any]] = None
    target_cfg: Optional[dict[str, Any]] = None


def build_classification_dataset(df: pd.DataFrame, spec: TaskSpec) -> pd.DataFrame:
    """
    Output columns:
    - prompt (string)
    - label (string)
    - plus passthrough metadata columns (scenario_id, __source_file, etc.)
    """
    cfg = ClassificationPromptConfig(**(spec.prompt_cfg or {}))
    rows = df.to_dict(orient="records")
    prompts = [build_classification_prompt(r, cfg=cfg) for r in rows]
    labels = [str(r.get(spec.target_col, "")) for r in rows]

    out = df.copy()
    out[spec.prompt_col_out] = prompts
    out[spec.label_col_out] = labels
    return out


def build_generation_dataset(df: pd.DataFrame, spec: TaskSpec) -> pd.DataFrame:
    """
    Output columns:
    - prompt (string)
    - target (string)
    - text (optional; concatenated training example if chat format enabled)
    """
    prompt_cfg = RemediationPromptConfig(**(spec.prompt_cfg or {}))
    rows = df.to_dict(orient="records")

    prompts = [build_generation_prompt(r, cfg=prompt_cfg) for r in rows]
    targets = [format_remediation_target(r, **(spec.target_cfg or {})) for r in rows]

    out = df.copy()
    out[spec.prompt_col_out] = prompts
    out["target"] = targets

    if spec.use_chat_format:
        text = [
            build_chatml_text(
                system_prompt=prompt_cfg.system_prompt,
                user_prompt=p.replace(f"System: {prompt_cfg.system_prompt}\n", ""),
                assistant_text=t,
            )
            for p, t in zip(prompts, targets)
        ]
        out[spec.text_col_out] = text

    return out


REGISTRY: Dict[str, BuilderFn] = {
    "classification": build_classification_dataset,
    "generation": build_generation_dataset,
}


def build_task_dataset(df: pd.DataFrame, spec: TaskSpec) -> pd.DataFrame:
    if spec.task_type not in REGISTRY:
        raise KeyError(
            f"Unknown task_type={spec.task_type!r}. "
            f"Available: {sorted(REGISTRY.keys())}"
        )
    return REGISTRY[spec.task_type](df, spec)

