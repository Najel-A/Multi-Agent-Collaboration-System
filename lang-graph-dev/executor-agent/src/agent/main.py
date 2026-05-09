from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI
from pydantic import BaseModel, Field

from agent.graph import graph


app = FastAPI(title="K8s Executor Agent")


class ExecuteRequest(BaseModel):
    alert: Dict[str, Any] = Field(default_factory=dict)

    diagnosis_1: str = ""
    diagnosis_2: str = ""
    chosen_diagnosis: str = ""

    namespace: str = "test-app"
    pod: str = ""
    deployment: str = ""
    container: str = ""

    approval: bool = False
    selected_action: Dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/execute")
async def execute(request: ExecuteRequest) -> Dict[str, Any]:
    result = await graph.ainvoke(request.model_dump())

    return {
        "status": result.get("status"),
        "message": result.get("message"),
        "proposed_actions": result.get("proposed_actions", []),
        "selected_action": result.get("selected_action", {}),
        "execution_result": result.get("execution_result", {}),
    }