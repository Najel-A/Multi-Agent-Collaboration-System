"""Model-client loaders — adapters between the orchestrator and an inference server.

A loader is a factory `(role, name) -> callable(prompt) -> text`. The orchestrator
calls `model_loader(role, name)` once per agent slot to get a callable it can
invoke with a prompt string. Swap the loader to swap the backend — the agents
and orchestrator don't change.

Current adapters:
    ollama_loader(base_url)        → local Ollama daemon
    vllm_loader(base_url, api_key) → vLLM (or any OpenAI-compatible endpoint)

Both return a loader compatible with Orchestrator.from_bootstrap() and
Orchestrator.from_role_defaults().
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable


ModelFn = Callable[[str], str]
ModelLoader = Callable[[str, str], ModelFn]


# ---------------------------------------------------------------------------
# Ollama (local, free, best for dev)
# ---------------------------------------------------------------------------

def ollama_loader(
    base_url: str = "http://localhost:11434",
    *,
    timeout: float = 120.0,
    temperature: float = 0.2,
    num_ctx: int = 8192,
) -> ModelLoader:
    """Build a loader that routes all calls to a local Ollama daemon.

    Usage:
        from agents.orchestrator import Orchestrator
        from agents.model_loaders import ollama_loader

        o = Orchestrator.from_bootstrap(ollama_loader())
        result = o.analyze(incident)

    Ollama must be running (`ollama serve`) with the named model pulled
    (`ollama pull qwen3.5:9b`). The `name` passed by the orchestrator is
    used verbatim as the Ollama model tag.

    Temperature defaults to 0.2 — RCA/executor/validator outputs should be
    deterministic-ish; bump to 0.5-0.7 only if you want more reasoning diversity
    between Agent 1 and Agent 2 in bootstrap mode.
    """
    endpoint = base_url.rstrip("/") + "/api/generate"

    def load(role: str, name: str) -> ModelFn:
        def call(prompt: str) -> str:
            payload = json.dumps({
                "model": name,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_ctx": num_ctx,
                },
            }).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            except urllib.error.URLError as e:
                raise RuntimeError(
                    f"Ollama call failed for model {name!r} at {endpoint}: {e}. "
                    f"Is `ollama serve` running and is the model pulled?"
                ) from e
            return body.get("response", "") or ""
        return call

    return load


# ---------------------------------------------------------------------------
# vLLM / any OpenAI-compatible endpoint (production)
# ---------------------------------------------------------------------------

def vllm_loader(
    base_url: str,
    *,
    api_key: str | None = None,
    timeout: float = 120.0,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> ModelLoader:
    """Build a loader that routes all calls to a vLLM (OpenAI-compatible) server.

    Works with any server that implements /v1/chat/completions — vLLM, TGI,
    LocalAI, Together AI, Fireworks, etc. The `name` becomes the `model` field.

    Usage:
        loader = vllm_loader("http://vllm.internal:8000/v1", api_key="...")
        o = Orchestrator.from_role_defaults(loader)
    """
    endpoint = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def load(role: str, name: str) -> ModelFn:
        def call(prompt: str) -> str:
            payload = json.dumps({
                "model": name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }).encode("utf-8")
            req = urllib.request.Request(
                endpoint, data=payload, headers=headers, method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            except urllib.error.URLError as e:
                raise RuntimeError(
                    f"vLLM call failed for model {name!r} at {endpoint}: {e}"
                ) from e
            choices = body.get("choices") or []
            if not choices:
                return ""
            return choices[0].get("message", {}).get("content", "") or ""
        return call

    return load
