"""FastAPI Inference Service — the 'FastAPI Inference Service' box in the
end-to-end architecture diagram.

Stateless RPC. Receives an incident from the Backend API tier, runs the
4-agent orchestrator, returns a Structured RCA Result. The service does
NOT:
    - retrieve incidents from a data store          (Backend API's job)
    - persist results                                (Backend API's job)
    - serve the frontend dashboard                   (Frontend's job)
    - authenticate end users                         (Backend API's job)

It only authenticates *the calling service* via an optional X-API-Key.

Endpoints:
    POST /analyze   structured incident   → structured RCA result (JSON)
    POST /query     LSA-WebApp prompt     → text with five RCA sections
    GET  /health    process liveness
    GET  /ready     model-backend reachability

Configure via env vars:
    RCA_BACKEND   = stub | ollama | vllm   (default: stub)
    OLLAMA_URL    = http://localhost:11434
    OLLAMA_MODEL  = qwen3.5:9b
    VLLM_URL      = http://vllm.internal:8000/v1
    VLLM_API_KEY  = ...
    VLLM_MODEL    = qwen3.5:9b
    RCA_API_KEY   = <key>  (optional; if set, requests must send X-API-Key)

Run:
    pip install fastapi uvicorn
    uvicorn api.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from agents.model_loaders import ollama_loader, vllm_loader
from agents.orchestrator import Orchestrator, format_as_sections


# ---------------------------------------------------------------------------
# Service-to-service auth (optional)
# ---------------------------------------------------------------------------

_API_KEY = os.environ.get("RCA_API_KEY")


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """If RCA_API_KEY is set, every protected endpoint requires the header."""
    if not _API_KEY:
        return
    if x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


# ---------------------------------------------------------------------------
# Backend / orchestrator setup
# ---------------------------------------------------------------------------

def _stub_loader():
    """Canned SFT-shaped responses per role. Used when RCA_BACKEND is unset
    or 'stub' — lets the inference service answer without any model running."""
    RCA = (
        "Pod is Pending because a referenced resource (Secret / ConfigMap "
        "key / image tag) is missing. See the event message for the specific "
        "identifier."
    )
    RECON = """## Diagnosis
The pod cannot start because a referenced Kubernetes resource does not exist. See the event_message for the specific missing resource (Secret / ConfigMap key / image tag).

## Fix plan
1. Create / correct the missing resource named in the event
2. Trigger the pod to retry container creation (delete pod or patch the resource)
3. Confirm the pod transitions to Running

## Commands
- kubectl -n <namespace> describe pod <pod_name>
- kubectl -n <namespace> get events --sort-by=.lastTimestamp | tail -20
- kubectl -n <namespace> get pod <pod_name> -w

## Notes
Stub response — set RCA_BACKEND=ollama or vllm for real model inference."""
    VALID = """## Verification
- Pod transitions from Pending to Running
- No new error events on describe
- Container logs show successful startup

## Rollback
- kubectl -n <namespace> rollout undo deployment/<name>
- Note: rollback may re-break the pod if the fix was the underlying dependency."""

    mapping = {
        "rca":       lambda p: RCA,
        "executor":  lambda p: RECON,
        "validator": lambda p: VALID,
    }
    return lambda role, name: mapping[role]


def _build_orchestrator() -> Orchestrator:
    backend = os.environ.get("RCA_BACKEND", "stub").lower()
    if backend == "stub":
        loader = _stub_loader()
        # Construct directly so each slot gets its role-specific stub
        # (from_bootstrap collapses all slots to one callable).
        return Orchestrator(
            agent_1_model    = loader("rca",       "stub"),
            agent_2_model    = loader("rca",       "stub"),
            reconciler_model = loader("executor",  "stub"),
            validator_model  = loader("validator", "stub"),
        )
    if backend == "ollama":
        loader = ollama_loader(
            base_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        )
        model = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
        return Orchestrator.from_bootstrap(loader, model=model)
    if backend == "vllm":
        url = os.environ.get("VLLM_URL")
        if not url:
            raise RuntimeError("RCA_BACKEND=vllm requires VLLM_URL")
        loader = vllm_loader(base_url=url, api_key=os.environ.get("VLLM_API_KEY"))
        model = os.environ.get("VLLM_MODEL", "qwen3.5:9b")
        return Orchestrator.from_bootstrap(loader, model=model)
    raise RuntimeError(f"unknown RCA_BACKEND: {backend!r}")


orchestrator = _build_orchestrator()


# ---------------------------------------------------------------------------
# Backend reachability probe (for /ready)
# ---------------------------------------------------------------------------

def _probe_backend(timeout: float = 2.0) -> tuple[bool, str]:
    backend = os.environ.get("RCA_BACKEND", "stub").lower()
    if backend == "stub":
        return True, "stub"
    if backend == "ollama":
        url = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/") + "/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.status == 200, f"ollama {resp.status}"
        except (urllib.error.URLError, TimeoutError) as e:
            return False, f"ollama unreachable: {e}"
    if backend == "vllm":
        base = os.environ.get("VLLM_URL", "").rstrip("/")
        if not base:
            return False, "VLLM_URL not set"
        try:
            req = urllib.request.Request(base + "/models")
            api_key = os.environ.get("VLLM_API_KEY")
            if api_key:
                req.add_header("Authorization", f"Bearer {api_key}")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200, f"vllm {resp.status}"
        except (urllib.error.URLError, TimeoutError) as e:
            return False, f"vllm unreachable: {e}"
    return False, f"unknown backend: {backend!r}"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    """Structured incident from the Backend API tier — flat schema matching
    data/02-raw/k8s_combined_incidents.jsonl. All fields optional; the
    orchestrator reads whatever is present."""
    scenario_id: str | None = None
    namespace: str | None = None
    pod_name: str | None = None
    pod_status: str | None = None
    event_reason: str | None = None
    event_message: str | None = None
    pod_describe: str | None = None
    pod_logs: str | None = None
    pod_logs_previous: str | None = None
    model_config = {"extra": "allow"}


class QueryRequest(BaseModel):
    """LSA-WebApp's NexusAnalyzeRequestBody contract.

    The frontend bundles the incident evidence into `prompt`, optionally
    between `--- INCIDENT EVIDENCE ---` markers. system_prompt /
    temperature / top_p / max_new_tokens are accepted but currently unused
    — the orchestrator's prompts are role-fixed. max_time becomes the
    pipeline-wide timeout for this single request.
    """
    system_prompt: str = ""
    prompt: str
    max_new_tokens: int = 1024
    max_time: float = 300.0
    temperature: float = 0.0
    top_p: float = 1.0


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Multi-Agent RCA Inference Service",
    description="Stateless 4-agent RCA pipeline — the 'FastAPI Inference Service' tier.",
    version="1.0.0",
)


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness — 200 if the process is running."""
    return {"status": "ok", "backend": os.environ.get("RCA_BACKEND", "stub")}


@app.get("/ready")
def ready() -> dict[str, Any]:
    """Readiness — probes the configured model backend; 503 if unreachable."""
    ok, detail = _probe_backend()
    if not ok:
        raise HTTPException(status_code=503, detail=detail)
    return {"status": "ready", "backend_detail": detail}


@app.post("/analyze", dependencies=[Depends(require_api_key)])
def analyze(req: AnalyzeRequest) -> dict[str, Any]:
    """Run the 4-agent pipeline on a structured incident; return the
    StructuredRCAResult as JSON. The Backend API tier is responsible for
    persisting the result and routing it back to the frontend."""
    incident = req.model_dump(exclude_none=True)
    if not incident.get("pod_describe") and not incident.get("event_message"):
        raise HTTPException(
            status_code=400,
            detail="incident must include at least pod_describe or event_message",
        )
    result = orchestrator.analyze(incident)
    return result.to_dict()


_EVIDENCE_START = "--- INCIDENT EVIDENCE ---"
_EVIDENCE_END = "--- END EVIDENCE ---"


def _extract_evidence(prompt: str) -> str:
    """Pull the text between LSA-WebApp's evidence markers; fall back to the
    full prompt if markers are absent."""
    start_idx = prompt.find(_EVIDENCE_START)
    end_idx = prompt.find(_EVIDENCE_END)
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        return prompt[start_idx + len(_EVIDENCE_START):end_idx].strip()
    return prompt.strip()


@app.post("/query", dependencies=[Depends(require_api_key)])
def query(req: QueryRequest) -> dict[str, str]:
    """LSA-WebApp contract: free-form prompt → text response with the five
    required section headers (Diagnosis / Step-by-Step Fix Plan / Concrete
    Actions or Commands to Apply the Fix / Verification Steps to Confirm
    the Fix Worked / Rollback Guidance if the Fix Causes Issues).

    Wraps the same orchestrator pipeline as /analyze, then flattens the
    structured result with format_as_sections() so the frontend's
    parseRcaResponse picks up every section.
    """
    evidence = _extract_evidence(req.prompt)
    if not evidence:
        raise HTTPException(status_code=400, detail="prompt missing incident evidence")
    incident = {
        "scenario_id": "query",
        "pod_describe": evidence,
    }
    result = orchestrator.analyze(incident, total_timeout_s=req.max_time)
    return {"text": format_as_sections(result)}
