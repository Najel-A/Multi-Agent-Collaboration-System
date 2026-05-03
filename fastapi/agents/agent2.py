"""Agent 2 — RCA generator B.

Stubbed: parallel diagnostician with slightly different framing and
latency from agent_1, so the orchestrator's `asyncio.gather` step
produces visibly distinct outputs.
"""

from __future__ import annotations

import asyncio

from schemas.responses import AgentDiagnosis
from services.memory import IncidentBlackboard

_LATENCY_S = 0.10


async def run(bb: IncidentBlackboard, incident_id: str) -> AgentDiagnosis:
    evidence = await bb.read(incident_id, "evidence_text") or ""
    await asyncio.sleep(_LATENCY_S)

    diagnosis = AgentDiagnosis(
        agent="agent_2",
        diagnosis=(
            "Container is unable to reach Running state — most likely the pod "
            "depends on a Kubernetes object that does not exist (missing Secret, "
            "missing ConfigMap key, or unreachable image registry / tag). "
            "Check the event_message and pod_describe for the exact identifier."
        ),
        confidence=0.78,
        notes=f"agent_2 stub; evidence_len={len(evidence)}",
    )
    await bb.write(incident_id, "agent_2_output", diagnosis)
    return diagnosis
