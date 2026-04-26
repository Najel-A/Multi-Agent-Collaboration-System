"""TriageAgent — opens the bidding round for one incident.

Responsibility:
    1. Inspect the incoming incident's event_reason.
    2. Emit a "handles" tag the AgentRegistry can match against.

The TriageAgent is intentionally model-free. Its job is pure routing —
fast, deterministic, easy to test. The actual reasoning happens
downstream in the RCA generators that bid for the incident.
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import AgentResult, BaseAgent


class TriageAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(name="triage", model=None)

    def run(self, incident: dict[str, Any]) -> AgentResult:
        reason = (incident.get("event_reason") or "").strip()
        handles = reason if reason else "*"
        return AgentResult(
            agent_name=self.name,
            status="success",
            findings={"handles": handles},
            confidence=1.0,
        )
