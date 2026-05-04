"""Agent 1 — RCA generator A.

When `RCA_QWEN_URL` is set, calls the deveshs18/rca-lora-qwen model and
uses its output as the diagnosis. Otherwise stays in stub mode (canned
text) so unit tests and demos without a model server still work.
"""

from __future__ import annotations

import asyncio

import httpx

from schemas.responses import AgentDiagnosis
from services.memory import IncidentBlackboard
from services.model_client import ModelEndpoint, call_model

_LATENCY_S = 0.15  # stub-mode pretend-latency
_ENDPOINT = ModelEndpoint.from_env(
    name="agent_1",
    url_env="RCA_QWEN_URL",
    model_env="RCA_QWEN_MODEL",
)

_STUB_DIAGNOSIS = (
    "Pod is failing to start because a referenced Kubernetes resource "
    "(Secret, ConfigMap key, or image tag) is missing. The specific "
    "missing resource is named in the event message."
)

_PROMPT_TEMPLATE = """You are a Kubernetes root-cause-analysis agent. Read the incident evidence and produce a single concise diagnostic paragraph identifying the most likely root cause. Do not include a fix plan or commands.

Incident evidence:
{evidence}

Diagnosis:"""


async def run(bb: IncidentBlackboard, incident_id: str) -> AgentDiagnosis:
    evidence = await bb.read(incident_id, "evidence_text") or ""
    diagnosis_text, mode = await _diagnose(evidence)

    diagnosis = AgentDiagnosis(
        agent="agent_1",
        diagnosis=diagnosis_text,
        confidence=0.82,
        notes=f"agent_1 {mode}; evidence_len={len(evidence)}",
    )
    await bb.write(incident_id, "agent_1_output", diagnosis)
    return diagnosis


async def _diagnose(evidence: str) -> tuple[str, str]:
    if _ENDPOINT is None:
        await asyncio.sleep(_LATENCY_S)
        return _STUB_DIAGNOSIS, "stub"
    try:
        prompt = _PROMPT_TEMPLATE.format(evidence=evidence)
        text = await call_model(_ENDPOINT, prompt, max_tokens=1024)
        if not text:
            return _STUB_DIAGNOSIS, "real-empty-fallback"
        return text, "real"
    except (httpx.HTTPError, httpx.RequestError) as e:
        return f"{_STUB_DIAGNOSIS} (model call failed: {e!r})", "real-error-fallback"
