"""Validation agent.

When `VALIDATOR_URL` is set, calls the mrunalikatta/validator-llama-3b
model and uses its output as the safety_notes string. Verification and
rollback step lists stay canned for now — structured-output parsing from
a free-text model is brittle until the API contract is confirmed.

When `VALIDATOR_URL` is unset, behaves exactly as the original stub.
"""

from __future__ import annotations

import asyncio

import httpx

from schemas.responses import ReconcilerOutput, ValidatorOutput
from services.memory import IncidentBlackboard
from services.model_client import ModelEndpoint, call_model

_LATENCY_S = 0.08
_ENDPOINT = ModelEndpoint.from_env(
    name="validator",
    url_env="VALIDATOR_URL",
    model_env="VALIDATOR_MODEL",
)

_STUB_VERIFICATION = [
    "Pod transitions from Pending/CrashLoopBackOff to Running.",
    "No new error events appear under `kubectl describe pod`.",
    "Container logs show normal startup — no fatal errors.",
]
_STUB_ROLLBACK = [
    "kubectl -n <namespace> rollout undo deployment/<name>",
    "Re-create or restore the previously-removed resource if the fix removed it.",
]
_STUB_SAFETY_BASE = (
    "Stub validator. Human review required because the reconciler "
    "proposed commands that mutate cluster state."
)
_STUB_SAFETY_NO_COMMANDS = (
    "No mutating commands proposed; review optional."
)

_PROMPT_TEMPLATE = """You are a Kubernetes safety validator. Given the diagnosis and proposed remediation commands below, produce a one-paragraph safety assessment. Note any commands that mutate cluster state, any preconditions that must hold before running them, and any obvious risks.

Diagnosis:
{diagnosis}

Proposed commands:
{commands}

Safety assessment:"""


async def run(bb: IncidentBlackboard, incident_id: str) -> ValidatorOutput:
    rec: ReconcilerOutput = await bb.read(incident_id, "reconciler_output")
    if rec is None:
        raise RuntimeError(
            f"validator: missing reconciler_output for {incident_id!r}"
        )

    has_commands = bool(rec.commands)
    safety_notes, mode = await _assess(rec, has_commands)

    output = ValidatorOutput(
        verification=list(_STUB_VERIFICATION),
        rollback=list(_STUB_ROLLBACK),
        requires_human_review=has_commands,
        safety_notes=f"[{mode}] {safety_notes}",
    )
    await bb.write(incident_id, "validation_output", output)
    return output


async def _assess(rec: ReconcilerOutput, has_commands: bool) -> tuple[str, str]:
    fallback = _STUB_SAFETY_BASE if has_commands else _STUB_SAFETY_NO_COMMANDS
    if _ENDPOINT is None:
        await asyncio.sleep(_LATENCY_S)
        return fallback, "stub"
    try:
        prompt = _PROMPT_TEMPLATE.format(
            diagnosis=rec.diagnosis,
            commands="\n".join(f"- {c}" for c in rec.commands) or "(none)",
        )
        text = await call_model(_ENDPOINT, prompt, max_tokens=600)
        if not text:
            return fallback, "real-empty-fallback"
        return text, "real"
    except (httpx.HTTPError, httpx.RequestError) as e:
        return f"{fallback} (validator call failed: {e!r})", "real-error-fallback"
