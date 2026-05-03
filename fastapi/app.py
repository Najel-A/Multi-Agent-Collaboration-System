"""NexusTrace sanity-check inference service.

Single FastAPI app that orchestrates the full 4-agent pipeline
(Agent 1 + Agent 2 -> Reconciler -> Validator) using asyncio for
parallelism instead of threads + Redis pub/sub.

Run from INSIDE this directory so the local `fastapi/` folder does not
shadow the installed `fastapi` library:

    cd fastapi
    uvicorn app:app --host 0.0.0.0 --port 8001 --reload

Use port 8001 — the existing api/main.py service already binds 8000.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI

from schemas.requests import AnalyzeRequest
from schemas.responses import AnalyzeResponse
from services.memory import IncidentBlackboard
from services.orchestrator import run_pipeline

app = FastAPI(
    title="NexusTrace Inference (sanity)",
    description="Async 4-agent pipeline — sanity-check tier.",
    version="0.1.0",
)

_blackboard = IncidentBlackboard()


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "fastapi-sanity",
        "agents": ["agent_1", "agent_2", "reconciler", "validator"],
    }


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    incident_id = req.incident_id or uuid.uuid4().hex[:12]
    return await run_pipeline(_blackboard, incident_id, req.evidence_text)
