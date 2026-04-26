"""Validation Agent — emits verification checks and rollback guidance.

Consumes the reconciled diagnosis + fix plan + commands, and produces:
    - Verification: checks to confirm the fix worked
    - Rollback:     steps to revert if the fix causes new issues

Uses the Validator fine-tuned model (qwen3.5:35b by default — doubles as the
rollback model; or llama3.2:3b as a lightweight alternative).

The user-prompt shape matches build_validator() in
data/01-generation/generate_sft_by_role.py.

Reads injected key from the incident:
    _reconciled_solution : {"diagnosis", "fix_plan", "commands", ...}
"""

from typing import Any

from agents.base_agent import AgentResult, BaseAgent


SYSTEM_VALIDATOR = (
    "You are a Kubernetes Validation agent. "
    "Given the actions taken to remediate an incident, produce verification "
    "steps to confirm the fix worked and rollback guidance if the fix causes "
    "new issues."
)


class ValidationAgent(BaseAgent):
    """Produces verification + rollback from the reconciled action plan."""

    def __init__(self, model: Any = None):
        super().__init__(name="validator", model=model)

    def run(self, incident: dict[str, Any]) -> AgentResult:
        reconciled = incident.get("_reconciled_solution", {}) or {}
        describe = incident.get("pod_describe") or ""

        actions_text = self._format_actions(
            reconciled.get("fix_plan", []),
            reconciled.get("commands", []),
        )

        user_prompt = (
            "Given the remediation actions taken for this incident, "
            "provide verification steps and rollback guidance.\n\n"
            f"## Incident context\n```\n{describe}\n```\n\n"
            f"## Diagnosis\n{reconciled.get('diagnosis', '')}\n\n"
            f"## Actions taken\n{actions_text}\n\n"
            "Respond in exactly this format:\n"
            "## Verification\n- <check>\n...\n\n"
            "## Rollback\n- <step>\n..."
        )
        prompt = f"SYSTEM: {SYSTEM_VALIDATOR}\n\nUSER: {user_prompt}"

        empty = {"verification": [], "rollback": []}

        if self.model is None:
            return AgentResult(
                agent_name=self.name, status="success",
                findings=empty, confidence=0.0,
            )

        try:
            raw = self.model(prompt)
        except Exception as e:
            return AgentResult(
                agent_name=self.name, status="error",
                findings=empty, error=str(e),
            )

        parsed = self._parse_output(raw)
        return AgentResult(
            agent_name=self.name,
            status="success",
            findings=parsed,
            confidence=0.85,
            raw_output=raw or "",
        )

    @staticmethod
    def _format_actions(fix_plan: list[str], commands: list[str]) -> str:
        parts: list[str] = []
        if fix_plan:
            parts.append("### Fix plan")
            for i, s in enumerate(fix_plan, 1):
                parts.append(f"{i}. {s}")
        if commands:
            parts.append("### Commands")
            for c in commands:
                parts.append(f"- {c}")
        return "\n".join(parts) if parts else "(none)"

    @staticmethod
    def _parse_output(raw: str) -> dict[str, list[str]]:
        verification: list[str] = []
        rollback: list[str] = []
        current: list[str] | None = None

        for line in (raw or "").splitlines():
            stripped = line.strip()
            lower = stripped.lower()
            if lower.startswith("## verification") or lower.startswith("verification:"):
                current = verification
            elif lower.startswith("## rollback") or lower.startswith("rollback:"):
                current = rollback
            elif current is not None and stripped:
                cleaned = stripped.lstrip("0123456789.-) ").strip()
                if cleaned:
                    current.append(cleaned)

        return {"verification": verification, "rollback": rollback}
