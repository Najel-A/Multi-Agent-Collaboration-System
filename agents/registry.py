"""Agent Registry + Capability declarations.

Each agent registers itself with a Capability that declares:
    role:               "rca" | "executor" | "validator" | "triage"
    handles:            set of K8s event_reasons it claims to handle, or
                        {"*"} for "any incident"
    cost:               latency hint (lower = preferred when bids tie)
    confidence_floor:   minimum bid below which the agent should not be
                        selected even if it's the highest scorer

The Orchestrator's Contract Net protocol calls Registry.discover(role,
handles) to find candidates, then asks each one for a bid and selects
the top scorers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents.base_agent import BaseAgent


@dataclass
class Capability:
    role: str
    handles: set[str] = field(default_factory=lambda: {"*"})
    cost: float = 1.0
    confidence_floor: float = 0.0


@dataclass
class RegisteredAgent:
    agent: BaseAgent
    capability: Capability


class AgentRegistry:
    """Discovery of agents by role and incident-handles match."""

    def __init__(self) -> None:
        self._agents: list[RegisteredAgent] = []

    def register(self, agent: BaseAgent, capability: Capability) -> None:
        self._agents.append(RegisteredAgent(agent=agent, capability=capability))

    def discover(
        self,
        role: str,
        handles: str | None = None,
    ) -> list[RegisteredAgent]:
        """Return agents matching the role; optionally filter by handles match.

        An agent with handles={"*"} matches any handles tag.
        """
        out: list[RegisteredAgent] = []
        for r in self._agents:
            if r.capability.role != role:
                continue
            if (
                handles is None
                or "*" in r.capability.handles
                or handles in r.capability.handles
            ):
                out.append(r)
        return out

    def all(self) -> list[RegisteredAgent]:
        return list(self._agents)
