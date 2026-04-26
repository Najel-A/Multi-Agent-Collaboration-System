"""Solution Generator Agent — the 'Agent 1' and 'Agent 2' boxes in the pipeline.

Generates a candidate RCA diagnosis from a raw k8s incident. Two instances run
in parallel with different fine-tuned RCA models:

    Agent 1 → qwen3.5:9b       (SFT: data/sft/rca_qwen3_5_9b.jsonl)
    Agent 2 → deepseek-r1:8b   (SFT: data/sft/rca_deepseek_r1_8b.jsonl)

The user-prompt shape matches build_rca() in
data/01-generation/generate_sft_by_role.py so the model sees inputs in the
same format it was trained on.

Reads the flat k8s_combined_incidents.jsonl schema directly.
"""

from typing import Any

from agents.base_agent import AgentResult, BaseAgent


SYSTEM_RCA = (
    "You are a Kubernetes Root Cause Analysis (RCA) agent. "
    "Given kubectl describe output and container logs from a failing pod, "
    "identify the root cause of the incident. Provide a clear, concise "
    "diagnosis explaining what is wrong and why the pod is in its current state."
)


class SolutionGeneratorAgent(BaseAgent):
    """Produces a candidate RCA diagnosis for a single incident."""

    def __init__(self, name: str, model: Any = None):
        super().__init__(name=name, model=model)

    def run(self, incident: dict[str, Any]) -> AgentResult:
        describe = incident.get("pod_describe") or ""
        logs = incident.get("pod_logs") or incident.get("pod_logs_previous") or ""

        if not describe and not logs:
            return AgentResult(
                agent_name=self.name,
                status="success",
                findings={"diagnosis": "", "note": "no describe/logs available"},
                confidence=0.0,
            )

        user_prompt = self._build_user_prompt(describe, logs)
        prompt = f"SYSTEM: {SYSTEM_RCA}\n\nUSER: {user_prompt}"

        if self.model is None:
            return AgentResult(
                agent_name=self.name,
                status="success",
                findings={"diagnosis": "", "note": "no model configured"},
                confidence=0.0,
            )

        try:
            raw = self.model(prompt)
        except Exception as e:
            return AgentResult(
                agent_name=self.name,
                status="error",
                findings={"diagnosis": ""},
                error=str(e),
            )

        return AgentResult(
            agent_name=self.name,
            status="success",
            findings={"diagnosis": (raw or "").strip()},
            confidence=0.8,
            raw_output=raw or "",
        )

    @staticmethod
    def _build_user_prompt(describe: str, logs: str) -> str:
        return (
            "Analyze this Kubernetes incident and identify the root cause.\n\n"
            f"## kubectl describe pod\n```\n{describe}\n```\n\n"
            f"## Container logs\n```\n{logs}\n```"
        )
