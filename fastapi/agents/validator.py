"""Validation agent.

Reads the reconciler's output and emits verification + rollback steps.
Stubbed: canned steps. Sets requires_human_review=True whenever the
reconciler proposed any commands — for the sanity check we always gate
on human approval before anything runs.
"""

from __future__ import annotations

import asyncio

from schemas.responses import ReconcilerOutput, ValidatorOutput
from services.memory import IncidentBlackboard

_LATENCY_S = 0.08


async def run(bb: IncidentBlackboard, incident_id: str) -> ValidatorOutput:
    rec: ReconcilerOutput = await bb.read(incident_id, "reconciler_output")
    if rec is None:
        raise RuntimeError(
            f"validator: missing reconciler_output for {incident_id!r}"
        )

    await asyncio.sleep(_LATENCY_S)

    has_commands = bool(rec.commands)
    output = ValidatorOutput(
        verification=[
            "Pod transitions from Pending/CrashLoopBackOff to Running.",
            "No new error events appear under `kubectl describe pod`.",
            "Container logs show normal startup — no fatal errors.",
        ],
        rollback=[
            "kubectl -n <namespace> rollout undo deployment/<name>",
            "Re-create or restore the previously-removed resource if the fix removed it.",
        ],
        requires_human_review=has_commands,
        safety_notes=(
            "Stub validator. Human review required because the reconciler "
            "proposed commands that mutate cluster state."
            if has_commands
            else "No mutating commands proposed; review optional."
        ),
    )
    await bb.write(incident_id, "validation_output", output)
    return output
