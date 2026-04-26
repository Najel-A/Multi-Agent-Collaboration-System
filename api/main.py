"""FastAPI inference service for the multi-agent RCA pipeline.

Matches the pipeline architecture diagram:

    User → Frontend (React UI / dashboard)
            ↓
       Backend API                   ← THIS MODULE
       (Incident Retrieval /
        Orchestration / Feedback)
            ↓
       FastAPI Inference Service     ← THIS MODULE
            ↓
       Agent 1 / Agent 2             ← agents/solution_generator_agent.py
       Decision / Reconciliation     ← agents/reconciliation_agent.py
       Validation                    ← agents/validation_agent.py
            ↓
       Structured RCA Result         ← returned as JSON to the dashboard

Run locally:
    pip install fastapi uvicorn
    uvicorn api.main:app --reload --port 8000

Open the dashboard:
    http://localhost:8000/

Configure the model backend via env vars:
    RCA_BACKEND   = stub | ollama | vllm          (default: stub)
    OLLAMA_URL    = http://localhost:11434        (default)
    OLLAMA_MODEL  = qwen3.5:9b                    (bootstrap model name)
    VLLM_URL      = http://vllm.internal:8000/v1
    VLLM_API_KEY  = ...
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.model_loaders import ollama_loader, vllm_loader
from agents.orchestrator import Orchestrator


ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "02-raw" / "k8s_combined_incidents.jsonl"
DASHBOARD_HTML = Path(__file__).resolve().parent / "dashboard.html"


# ---------------------------------------------------------------------------
# Backend / orchestrator setup
# ---------------------------------------------------------------------------

def _stub_loader():
    """Canned SFT-shaped responses per role. Used when no model backend is set.

    Lets the UI work end-to-end with zero model setup — pastes the real
    pipeline output structure so the dashboard is exercisable from the moment
    `uvicorn` comes up.
    """
    RCA = "Pod is Pending because a referenced resource (Secret / ConfigMap key / image tag) is missing. See event message for the specific identifier."
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
Stub response — configure RCA_BACKEND=ollama or vllm for real model inference."""
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
        # Construct directly so each slot gets its role-specific canned response.
        # (from_bootstrap collapses all slots to a single callable, which is wrong
        # when the stub emits different shapes per role.)
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


# The orchestrator is built once at process startup. The agent instances are
# thread-safe for read — FastAPI can handle concurrent requests against them.
orchestrator = _build_orchestrator()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    """Incident payload. All fields are optional — the orchestrator reads
    whatever is present (flat k8s_combined_incidents.jsonl schema)."""
    scenario_id: str | None = None
    namespace: str | None = None
    pod_name: str | None = None
    pod_status: str | None = None
    event_reason: str | None = None
    event_message: str | None = None
    pod_describe: str | None = None
    pod_logs: str | None = None
    pod_logs_previous: str | None = None
    # Allow extra fields for future schema additions.
    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Multi-Agent RCA Inference Service",
    description="Wraps the 4-agent RCA pipeline behind an HTTP API.",
    version="0.1.0",
)


@app.get("/")
def dashboard() -> FileResponse:
    """Serve the minimal dashboard HTML at the root."""
    return FileResponse(DASHBOARD_HTML)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "backend": os.environ.get("RCA_BACKEND", "stub"),
        "data_path_exists": DATA_PATH.exists(),
    }


@app.get("/incidents/samples")
def list_sample_incidents(count: int = 5) -> list[dict[str, Any]]:
    """Return the first N incidents from the k8s_combined_incidents.jsonl file.

    Used by the dashboard to populate a 'Load sample' dropdown.
    """
    if not DATA_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=f"dataset not found at {DATA_PATH}",
        )
    out: list[dict[str, Any]] = []
    seen_scenarios: set[str] = set()
    with open(DATA_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            # Return one per scenario for variety
            if rec.get("scenario_id") in seen_scenarios:
                continue
            seen_scenarios.add(rec.get("scenario_id", ""))
            out.append(rec)
            if len(out) >= count:
                break
    return out


@app.post("/analyze")
def analyze(req: AnalyzeRequest) -> dict[str, Any]:
    """Run the full 4-agent pipeline on a single incident."""
    incident = req.model_dump(exclude_none=True)
    if not incident.get("pod_describe") and not incident.get("event_message"):
        raise HTTPException(
            status_code=400,
            detail="incident must include at least pod_describe or event_message",
        )
    result = orchestrator.analyze(incident)
    return result.to_dict()
