from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .dataset_builders import TaskSpec


@dataclass(frozen=True)
class PipelineSpec:
    """
    Minimal registry entry that can evolve.
    Add fields later for: model configs, trainer configs, eval configs, etc.
    """

    name: str
    task: TaskSpec
    description: str = ""
    metadata: Optional[dict[str, Any]] = None


PIPELINES: Dict[str, PipelineSpec] = {}


def register_pipeline(spec: PipelineSpec) -> None:
    if spec.name in PIPELINES:
        raise KeyError(f"Pipeline already registered: {spec.name}")
    PIPELINES[spec.name] = spec


def get_pipeline(name: str) -> PipelineSpec:
    if name not in PIPELINES:
        raise KeyError(f"Unknown pipeline: {name}. Available: {sorted(PIPELINES)}")
    return PIPELINES[name]


def register_default_pipelines() -> None:
    """
    Defaults that match your current project needs.
    Add new pipelines here without changing notebook logic.
    """
    from .dataset_builders import TaskSpec

    register_pipeline(
        PipelineSpec(
            name="scenario_classification_v1",
            description="Classify scenario_id from evidence_text.",
            task=TaskSpec(
                name="scenario_classification",
                task_type="classification",
                input_col="evidence_text",
                target_col="scenario_id",
            ),
        )
    )

    register_pipeline(
        PipelineSpec(
            name="remediation_generation_v1",
            description="Generate remediation sections from evidence_text.",
            task=TaskSpec(
                name="remediation_generation",
                task_type="generation",
                input_col="evidence_text",
                target_col="scenario_id",
                use_chat_format=True,
            ),
        )
    )

