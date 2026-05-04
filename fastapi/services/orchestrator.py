"""Async pipeline orchestrator.

Pipeline (matches the architecture diagram):

    Incident Evidence
            │
            ▼
    Shared Memory / Incident Blackboard
            │
        ┌───┴───┐
        ▼       ▼
    Agent 1  Agent 2          ← asyncio.gather (parallel)
        └───┬───┘
            ▼
    Reconciliation Agent      ← reads both, picks winner, builds fix plan
            │
            ▼
    Validation Agent          ← reads reconciler output, emits verification + rollback
            │
            ▼
    AnalyzeResponse           ← assembled from the four typed outputs

Per-step `asyncio.wait_for` caps each phase so a hung stub (or model call)
can't pin a request indefinitely. The cap reads from `MODEL_TIMEOUT_S`
(default 180s) — high enough for x86-emulated real models on Apple
Silicon, plenty for stubs.
"""

from __future__ import annotations

import asyncio
import os

from agents import agent1, agent2, reconciler, validator
from schemas.responses import AnalyzeResponse, FinalRecommendation
from services.memory import IncidentBlackboard

_STEP_TIMEOUT_S = float(os.environ.get("MODEL_TIMEOUT_S", "180"))


async def run_pipeline(
    bb: IncidentBlackboard,
    incident_id: str,
    evidence_text: str,
) -> AnalyzeResponse:
    await bb.init(incident_id, evidence_text)
    try:
        a1, a2 = await asyncio.wait_for(
            asyncio.gather(
                agent1.run(bb, incident_id),
                agent2.run(bb, incident_id),
            ),
            timeout=_STEP_TIMEOUT_S,
        )

        rec = await asyncio.wait_for(
            reconciler.run(bb, incident_id),
            timeout=_STEP_TIMEOUT_S,
        )

        val = await asyncio.wait_for(
            validator.run(bb, incident_id),
            timeout=_STEP_TIMEOUT_S,
        )

        final = FinalRecommendation(
            diagnosis=rec.diagnosis,
            fix_plan=list(rec.fix_plan),
            commands=list(rec.commands),
            verification=list(val.verification),
            rollback=list(val.rollback),
        )

        return AnalyzeResponse(
            incident_id=incident_id,
            agent_1_output=a1,
            agent_2_output=a2,
            reconciler_output=rec,
            validation_output=val,
            final_recommendation=final,
            requires_human_review=val.requires_human_review,
        )
    finally:
        await bb.discard(incident_id)
