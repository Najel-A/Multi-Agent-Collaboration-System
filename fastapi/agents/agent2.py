"""Agent 2 — RCA generator B.

When `RCA_DEEPSEEK_URL` is set, calls the deveshs18/rca-lora-deepseek
model and uses its output as the diagnosis. Otherwise stays in stub
mode for unit tests and demos.
"""

from __future__ import annotations

import asyncio

import httpx

from schemas.responses import AgentDiagnosis
from services.memory import IncidentBlackboard
from services.model_client import ModelEndpoint, call_model

_LATENCY_S = 0.10
_ENDPOINT = ModelEndpoint.from_env(
    name="agent_2",
    url_env="RCA_DEEPSEEK_URL",
    model_env="RCA_DEEPSEEK_MODEL",
)

_STUB_DIAGNOSIS = (
    "Container is unable to reach Running state — most likely the pod "
    "depends on a Kubernetes object that does not exist (missing Secret, "
    "missing ConfigMap key, or unreachable image registry / tag). "
    "Check the event_message and pod_describe for the exact identifier."
)

_PROMPT_TEMPLATE = """You are a Kubernetes root-cause-analysis agent. Read the incident evidence and produce a single concise diagnostic paragraph identifying the most likely root cause. Do not include a fix plan or commands.

Incident evidence:
{evidence}

Diagnosis:"""


async def run(bb: IncidentBlackboard, incident_id: str) -> AgentDiagnosis:
    evidence = await bb.read(incident_id, "evidence_text") or ""
    diagnosis_text, mode = await _diagnose(evidence)

    diagnosis = AgentDiagnosis(
        agent="agent_2",
        diagnosis=diagnosis_text,
        confidence=0.78,
        notes=f"agent_2 {mode}; evidence_len={len(evidence)}",
    )
    await bb.write(incident_id, "agent_2_output", diagnosis)
    return diagnosis


async def _diagnose(evidence: str) -> tuple[str, str]:
    if _ENDPOINT is None:
        await asyncio.sleep(_LATENCY_S)
        return _STUB_DIAGNOSIS, "stub"
    try:
        prompt = _PROMPT_TEMPLATE.format(evidence=evidence)
        # 1024 is intentional — deepseek-r1 spends 200-600 tokens on <think>
        # reasoning before the final answer. Smaller caps starve the answer.
        text = await call_model(_ENDPOINT, prompt, max_tokens=1024)
        if not text:
            return _STUB_DIAGNOSIS, "real-empty-fallback"
        return text, "real"
    except (httpx.HTTPError, httpx.RequestError) as e:
        return f"{_STUB_DIAGNOSIS} (model call failed: {e!r})", "real-error-fallback"
