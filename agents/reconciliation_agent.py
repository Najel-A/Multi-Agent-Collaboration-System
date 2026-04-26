"""Decision / Reconciliation Agent — picks or merges the two RCA candidate
solutions and emits the final fix plan + kubectl commands.

Uses the Executor fine-tuned model (devstral-small-2:24b — SFT:
data/sft/executor_devstral_24b.jsonl). Devstral is a code/command model,
so this step is responsible both for reconciling the two candidate
diagnoses AND for producing the copy-pasteable kubectl commands.

Reads injected keys from the incident:
    _agent_1_solution : {"diagnosis": ...}
    _agent_2_solution : {"diagnosis": ...}
Plus the flat k8s_combined_incidents.jsonl fields (pod_describe, etc.).
"""

from typing import Any

from agents.base_agent import AgentResult, BaseAgent


SYSTEM_RECONCILER = (
    "You are a Kubernetes SRE acting as a reconciliation agent. "
    "Two RCA agents have each produced a candidate diagnosis for the same "
    "incident. Select the better diagnosis (or synthesize a unified one), "
    "then produce an ordered fix plan and the exact kubectl commands needed "
    "to remediate. Commands must be safe, ordered, and copy-pasteable."
)


class ReconciliationAgent(BaseAgent):
    """Merges two candidate RCA solutions into a single action plan."""

    def __init__(self, model: Any = None):
        super().__init__(name="reconciler", model=model)

    def run(self, incident: dict[str, Any]) -> AgentResult:
        sol_1 = incident.get("_agent_1_solution", {}) or {}
        sol_2 = incident.get("_agent_2_solution", {}) or {}
        describe = incident.get("pod_describe") or ""

        user_prompt = self._build_user_prompt(
            describe=describe,
            diag_1=sol_1.get("diagnosis", ""),
            diag_2=sol_2.get("diagnosis", ""),
        )
        prompt = f"SYSTEM: {SYSTEM_RECONCILER}\n\nUSER: {user_prompt}"

        empty = {"diagnosis": "", "fix_plan": [], "commands": [], "notes": ""}

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
    def _build_user_prompt(describe: str, diag_1: str, diag_2: str) -> str:
        return (
            "Two RCA agents analyzed the same incident. Choose the better "
            "diagnosis (or synthesize a unified one), then produce the fix "
            "plan and the exact kubectl commands.\n\n"
            f"## Incident context\n```\n{describe}\n```\n\n"
            f"## Agent 1 diagnosis\n{diag_1 or '(empty)'}\n\n"
            f"## Agent 2 diagnosis\n{diag_2 or '(empty)'}\n\n"
            "Respond in exactly this format:\n"
            "## Diagnosis\n<final diagnosis prose>\n\n"
            "## Fix plan\n1. <step>\n2. <step>\n...\n\n"
            "## Commands\n- <command>\n...\n\n"
            "## Notes\n<which candidate you preferred and why>"
        )

    @staticmethod
    def _parse_output(raw: str) -> dict[str, Any]:
        sections: dict[str, Any] = {
            "diagnosis": "", "fix_plan": [], "commands": [], "notes": "",
        }
        current: str | None = None
        prose_buf: list[str] = []

        def flush_prose() -> None:
            if current in ("diagnosis", "notes") and prose_buf:
                sections[current] = "\n".join(prose_buf).strip()
            prose_buf.clear()

        for line in (raw or "").splitlines():
            stripped = line.strip()
            lower = stripped.lower()
            if lower.startswith("## diagnosis"):
                flush_prose(); current = "diagnosis"
            elif lower.startswith("## fix plan") or lower.startswith("## fix_plan"):
                flush_prose(); current = "fix_plan"
            elif lower.startswith("## commands"):
                flush_prose(); current = "commands"
            elif lower.startswith("## notes"):
                flush_prose(); current = "notes"
            elif current in ("fix_plan", "commands"):
                if stripped:
                    cleaned = stripped.lstrip("0123456789.-) ").strip()
                    if cleaned:
                        sections[current].append(cleaned)
            elif current in ("diagnosis", "notes"):
                if stripped:
                    prose_buf.append(stripped)

        flush_prose()
        return sections
