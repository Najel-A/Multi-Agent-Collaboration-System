"""Async HTTP client for calling external model inference services.

Targets the OpenAI-compatible **chat-completions** endpoint:

    POST {base_url}/v1/chat/completions
    {"model": str, "messages": [{"role":"user","content": prompt}], ...}
    -> {"choices": [{"message": {"content": str}}]}

This shape works for Ollama, vLLM, llama.cpp's HTTP server, and most
OpenAI-compat layers. If a target service expects /v1/completions or a
custom contract (e.g. the team's mrunalikatta/deveshs18 LoRA images
which use POST /v1/rca/generate with structured K8s fields), swap the
body of `call_model` below. Agents import only `call_model` and
`ModelEndpoint.from_env`, so changes here propagate without touching
agent code.

Env-driven endpoint configuration is intentional — agents fall back to
canned stubs whenever the relevant *_URL env var is unset, so the same
code runs in unit tests (no env, all stubs) and in production (env set,
real models). This is what keeps the 13-test suite green during wiring.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

DEFAULT_TIMEOUT_S = float(os.environ.get("MODEL_TIMEOUT_S", "180"))

# Reasoning models (deepseek-r1, qwen-with-thinking, etc.) emit their chain-
# of-thought inside <think>...</think> blocks. We strip those before returning
# so callers see only the final answer.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class ModelEndpoint:
    """One model service. Constructed from env vars or directly."""

    name: str
    url: str
    model_name: str = "default"
    timeout_s: float = DEFAULT_TIMEOUT_S

    @classmethod
    def from_env(
        cls,
        name: str,
        url_env: str,
        model_env: str | None = None,
    ) -> "ModelEndpoint | None":
        """Build an endpoint from env vars. Returns None if `url_env` is unset.

        Returning None is the signal to agents that they should fall back to
        stub behavior.
        """
        url = os.environ.get(url_env)
        if not url:
            return None
        model_name = (
            os.environ.get(model_env, "default") if model_env else "default"
        )
        return cls(name=name, url=url.rstrip("/"), model_name=model_name)


async def call_model(
    endpoint: ModelEndpoint,
    prompt: str,
    *,
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> str:
    """OpenAI-compatible chat-completion call.

    Wraps `prompt` in a single user message and POSTs to /v1/chat/completions.
    Returns the assistant's reply text, stripped of <think>...</think>
    reasoning blocks. Raises httpx.HTTPError on transport / non-2xx; the
    caller decides whether to fall back to a stub.

    Default max_tokens is 1024 — large enough that reasoning models like
    deepseek-r1 (which can spend 200-600 tokens on chain-of-thought before
    the final answer) still leave room for the actual response.
    """
    async with httpx.AsyncClient(timeout=endpoint.timeout_s) as client:
        resp = await client.post(
            f"{endpoint.url}/v1/chat/completions",
            json={
                "model": endpoint.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    # Standard OpenAI chat shape; tolerate the legacy completions shape too.
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    text = msg.get("content") or choices[0].get("text", "")
    # Strip reasoning-model <think>...</think> blocks before returning.
    text = _THINK_BLOCK_RE.sub("", text or "")
    return text.strip()
