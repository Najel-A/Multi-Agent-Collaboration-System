"""Reconciliation agent.

When `EXECUTOR_URL` is set, calls the mrunalikatta/executor-mistral-24b
model and uses its output as the merged diagnosis. The fix_plan and
commands stay canned for now — parsing structured output from a free-text
model is brittle; we'll harden that once the API contract is confirmed.

When `EXECUTOR_URL` is unset, behaves exactly as the original stub:
picks the higher-confidence diagnosis verbatim.
"""

from __future__ import annotations

import asyncio

import httpx

from schemas.responses import AgentDiagnosis, ReconcilerOutput
from services.memory import IncidentBlackboard
from services.model_client import ModelEndpoint, call_model

_LATENCY_S = 0.10
_ENDPOINT = ModelEndpoint.from_env(
    name="reconciler",
    url_env="EXECUTOR_URL",
    model_env="EXECUTOR_MODEL",
)

_STUB_FIX_PLAN = [
    "Identify the missing resource named in the pod event message.",
    "Create or correct the missing Secret / ConfigMap / image tag.",
    "Restart the pod (delete + let the Deployment recreate it) and confirm it reaches Running.",
]
_STUB_COMMANDS = [
    "kubectl -n <namespace> describe pod <pod_name>",
    "kubectl -n <namespace> get events --sort-by=.lastTimestamp | tail -20",
    "kubectl -n <namespace> delete pod <pod_name>",
    "kubectl -n <namespace> get pod <pod_name> -w",
]

_PROMPT_TEMPLATE = """You are a Kubernetes operations executor. Two RCA agents have proposed competing diagnoses for the same incident. Pick the more accurate one and produce a concise final diagnosis paragraph that synthesizes the best parts of both.

Agent 1 diagnosis (confidence 0.82):
{a1}

Agent 2 diagnosis (confidence 0.78):
{a2}

Final reconciled diagnosis:"""


async def run(bb: IncidentBlackboard, incident_id: str) -> ReconcilerOutput:
    a1: AgentDiagnosis = await bb.read(incident_id, "agent_1_output")
    a2: AgentDiagnosis = await bb.read(incident_id, "agent_2_output")
    if a1 is None or a2 is None:
        raise RuntimeError(
            f"reconciler: missing agent output(s) for {incident_id!r} "
            f"(agent_1={a1 is not None}, agent_2={a2 is not None})"
        )

    winner, loser = (a1, a2) if a1.confidence >= a2.confidence else (a2, a1)
    diagnosis_text, mode = await _reconcile(a1, a2, winner)

    output = ReconcilerOutput(
        diagnosis=diagnosis_text,
        fix_plan=list(_STUB_FIX_PLAN),
        commands=list(_STUB_COMMANDS),
        chosen_source=winner.agent,
        notes=(
            f"[{mode}] selected {winner.agent} (conf={winner.confidence:.2f}) "
            f"over {loser.agent} (conf={loser.confidence:.2f})."
        ),
    )
    await bb.write(incident_id, "reconciler_output", output)
    return output


async def _reconcile(
    a1: AgentDiagnosis,
    a2: AgentDiagnosis,
    winner: AgentDiagnosis,
) -> tuple[str, str]:
    if _ENDPOINT is None:
        await asyncio.sleep(_LATENCY_S)
        return winner.diagnosis, "stub"
    try:
        prompt = _PROMPT_TEMPLATE.format(a1=a1.diagnosis, a2=a2.diagnosis)
        text = await call_model(_ENDPOINT, prompt, max_tokens=800)
        if not text:
            return winner.diagnosis, "real-empty-fallback"
        return text, "real"
    except (httpx.HTTPError, httpx.RequestError) as e:
        return f"{winner.diagnosis} (executor call failed: {e!r})", "real-error-fallback"
