"""Request schemas for the NexusTrace sanity-check inference service."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class AnalyzeRequest(BaseModel):
    """Single-incident analyze request.

    Matches the contract documented in the fastapi/ README:
        {
          "incident_id": "optional-id",
          "evidence_text": "raw Kubernetes incident evidence"
        }
    """

    incident_id: str | None = Field(
        default=None,
        description="Caller-supplied incident id. Service generates one if omitted.",
        max_length=128,
    )
    evidence_text: str = Field(
        ...,
        description="Raw Kubernetes incident evidence (describe / events / logs blob).",
        min_length=1,
    )

    @field_validator("evidence_text")
    @classmethod
    def _evidence_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("evidence_text must not be blank")
        return v
