from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any, Dict

from agent.graph import graph

app = FastAPI(title="RCA LangGraph Agent")


class RCARequest(BaseModel):
    alert: Dict[str, Any] = {}
    raw_alert_text: str = ""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/rca")
async def run_rca(req: RCARequest):
    result = await graph.ainvoke({
        "alert": req.alert,
        "raw_alert_text": req.raw_alert_text,
    })

    return result