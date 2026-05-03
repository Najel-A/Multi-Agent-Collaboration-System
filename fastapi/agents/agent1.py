"""Agent 1 — RCA generator A.

Stubbed: returns a canned Kubernetes diagnosis after a short async sleep
to simulate model latency. Reads evidence from the blackboard, writes its
output back under 'agent_1_output', and returns the typed diagnosis.
"""

from __future__ import annotations

import asyncio

from schemas.responses import AgentDiagnosis
from services.memory import IncidentBlackboard

_LATENCY_S = 0.15


async def run(bb: IncidentBlackboard, incident_id: str) -> AgentDiagnosis:
    evidence = await bb.read(incident_id, "evidence_text") or ""
    await asyncio.sleep(_LATENCY_S)

    diagnosis = AgentDiagnosis(
        agent="agent_1",
        diagnosis=(
            "Pod is failing to start because a referenced Kubernetes resource "
            "(Secret, ConfigMap key, or image tag) is missing. The specific "
            "missing resource is named in the event message."
        ),
        confidence=0.82,
        notes=f"agent_1 stub; evidence_len={len(evidence)}",
    )
    await bb.write(incident_id, "agent_1_output", diagnosis)
    return diagnosis
