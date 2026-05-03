"""Reconciliation agent.

Reads agent_1_output and agent_2_output from the blackboard, picks the
higher-confidence diagnosis as the final one, and produces a fix plan +
concrete commands. Stubbed: the fix plan and commands are canned for the
sanity check.
"""

from __future__ import annotations

import asyncio

from schemas.responses import AgentDiagnosis, ReconcilerOutput
from services.memory import IncidentBlackboard

_LATENCY_S = 0.10


async def run(bb: IncidentBlackboard, incident_id: str) -> ReconcilerOutput:
    a1: AgentDiagnosis = await bb.read(incident_id, "agent_1_output")
    a2: AgentDiagnosis = await bb.read(incident_id, "agent_2_output")
    if a1 is None or a2 is None:
        raise RuntimeError(
            f"reconciler: missing agent output(s) for {incident_id!r} "
            f"(agent_1={a1 is not None}, agent_2={a2 is not None})"
        )

    await asyncio.sleep(_LATENCY_S)

    winner, loser = (a1, a2) if a1.confidence >= a2.confidence else (a2, a1)
    chosen_source = winner.agent

    output = ReconcilerOutput(
        diagnosis=winner.diagnosis,
        fix_plan=[
            "Identify the missing resource named in the pod event message.",
            "Create or correct the missing Secret / ConfigMap / image tag.",
            "Restart the pod (delete + let the Deployment recreate it) and confirm it reaches Running.",
        ],
        commands=[
            "kubectl -n <namespace> describe pod <pod_name>",
            "kubectl -n <namespace> get events --sort-by=.lastTimestamp | tail -20",
            "kubectl -n <namespace> delete pod <pod_name>",
            "kubectl -n <namespace> get pod <pod_name> -w",
        ],
        chosen_source=chosen_source,
        notes=(
            f"Selected {chosen_source} (conf={winner.confidence:.2f}) over "
            f"{loser.agent} (conf={loser.confidence:.2f}). Stub reconciliation."
        ),
    )
    await bb.write(incident_id, "reconciler_output", output)
    return output
