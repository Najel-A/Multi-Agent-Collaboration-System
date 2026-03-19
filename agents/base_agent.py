"""Base agent interface for all specialist agents."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResult:
    """Standard result returned by every agent."""
    agent_name: str
    status: str  # "success" | "error"
    findings: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0  # 0.0 - 1.0
    raw_output: str = ""
    error: str | None = None


class BaseAgent(ABC):
    """Abstract base for all RCA agents.

    Subclasses implement `run()` which receives an incident dict
    and returns an AgentResult.  The `model` attribute can be
    swapped between a local fine-tuned model and an API-based LLM.
    """

    def __init__(self, name: str, model: Any = None):
        self.name = name
        self.model = model  # injected model / client

    @abstractmethod
    def run(self, incident: dict[str, Any]) -> AgentResult:
        """Analyse the incident and return findings."""

    def _build_prompt(self, incident: dict[str, Any]) -> str:
        """Build the prompt sent to the model. Override per agent."""
        return ""

    def _call_model(self, prompt: str) -> str:
        """Send prompt to the model and return raw text.

        Default: returns the prompt itself (stub).
        Replace with real inference once models are trained.
        """
        if self.model is None:
            return self._rule_based_fallback(prompt)
        # When a real model is plugged in:
        #   return self.model.generate(prompt)
        return self.model(prompt)

    def _rule_based_fallback(self, prompt: str) -> str:
        """Override in subclasses to provide rule-based logic
        when no model is available (useful for testing)."""
        return ""
