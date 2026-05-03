"""Response schemas for the NexusTrace sanity-check inference service.

Each agent's output is a typed sub-model so the contract stays explicit
instead of bare dicts. The orchestrator assembles AnalyzeResponse from
the blackboard snapshot at the end of the pipeline.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentDiagnosis(BaseModel):
    """Output shape for Agent 1 / Agent 2 (the two RCA generators)."""

    agent: str = Field(..., description="Agent identifier, e.g. 'agent_1'.")
    diagnosis: str = Field(..., description="One-paragraph root-cause hypothesis.")
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Self-reported confidence in [0, 1].",
    )
    notes: str = Field(default="", description="Free-form reasoning notes.")


class ReconcilerOutput(BaseModel):
    """Reconciler merges/picks across the two agent diagnoses and produces
    the proposed remediation."""

    diagnosis: str = Field(..., description="Final reconciled diagnosis.")
    fix_plan: list[str] = Field(
        default_factory=list,
        description="Ordered remediation steps in plain language.",
    )
    commands: list[str] = Field(
        default_factory=list,
        description="Concrete shell / kubectl commands implementing the fix.",
    )
    chosen_source: str = Field(
        default="",
        description="Which agent's diagnosis won, or 'merged' if combined.",
    )
    notes: str = Field(default="", description="Reconciliation rationale.")


class ValidatorOutput(BaseModel):
    """Validator checks the reconciled plan and emits verification + rollback."""

    verification: list[str] = Field(
        default_factory=list,
        description="Steps to confirm the fix worked.",
    )
    rollback: list[str] = Field(
        default_factory=list,
        description="Steps to undo the fix if it causes regression.",
    )
    requires_human_review: bool = Field(
        default=True,
        description="True if a human must approve before commands run.",
    )
    safety_notes: str = Field(
        default="",
        description="Why review is or isn't required, or other safety flags.",
    )


class FinalRecommendation(BaseModel):
    """Flattened, caller-friendly view of the reconciled + validated result."""

    diagnosis: str
    fix_plan: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    rollback: list[str] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    """Full pipeline response — every agent's output plus the merged view."""

    incident_id: str
    agent_1_output: AgentDiagnosis
    agent_2_output: AgentDiagnosis
    reconciler_output: ReconcilerOutput
    validation_output: ValidatorOutput
    final_recommendation: FinalRecommendation
    requires_human_review: bool
