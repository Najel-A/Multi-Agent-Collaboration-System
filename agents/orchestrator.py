"""Orchestrator — multi-agent RCA pipeline.

Pipeline (matches the architecture diagram):

    Incident
        │
        ├──► Agent 1 Solution Generator   (RCA: qwen3.5:9b)
        └──► Agent 2 Solution Generator   (RCA: deepseek-r1:8b)
                         │
                         ▼
        Decision / Reconciliation Agent   (Executor: devstral-small-2:24b)
                         │
                         ▼
               Validation Agent           (Validator: qwen3.5:35b | llama3.2:3b)
                         │
                         ▼
             Structured RCA Result
        (diagnosis / fix_plan / commands / verification / rollback)

Data source: data/02-raw/k8s_combined_incidents.jsonl (flat 20-field schema).
SFT data:    data/sft/{rca,executor,validator}_*.jsonl (see
             data/01-generation/generate_sft_by_role.py for prompt shapes).
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from agents.base_agent import AgentResult
from agents.solution_generator_agent import SolutionGeneratorAgent
from agents.reconciliation_agent import ReconciliationAgent
from agents.validation_agent import ValidationAgent


# ---------------------------------------------------------------------------
# Approved SFT-trained models per role.
# ---------------------------------------------------------------------------

AGENT_ROLE_MODELS: dict[str, tuple[str, ...]] = {
    "rca":       ("qwen3.5:9b", "deepseek-r1:8b"),
    "executor":  ("devstral-small-2:24b",),
    "validator": ("qwen3.5:35b", "llama3.2:3b"),
}

DEFAULT_PIPELINE: dict[str, str] = {
    "agent_1":    "qwen3.5:9b",
    "agent_2":    "deepseek-r1:8b",
    "reconciler": "devstral-small-2:24b",
    "validator":  "qwen3.5:35b",  # also doubles as the rollback model
}


# ---------------------------------------------------------------------------
# Final pipeline output
# ---------------------------------------------------------------------------

@dataclass
class StructuredRCAResult:
    """The 'Structured RCA Result' box in the diagram."""
    incident_id: str
    diagnosis: str
    fix_plan: list[str]
    commands: list[str]
    verification: list[str]
    rollback: list[str]
    agent_1_solution: dict[str, Any] = field(default_factory=dict)
    agent_2_solution: dict[str, Any] = field(default_factory=dict)
    reconciliation_notes: str = ""
    agent_results: dict[str, AgentResult] = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id":          self.incident_id,
            "diagnosis":            self.diagnosis,
            "fix_plan":             self.fix_plan,
            "commands":             self.commands,
            "verification":         self.verification,
            "rollback":             self.rollback,
            "agent_1":              self.agent_1_solution,
            "agent_2":              self.agent_2_solution,
            "reconciliation_notes": self.reconciliation_notes,
            "duration_ms":          self.duration_ms,
        }

    def summary(self) -> str:
        lines = [
            f"=== RCA Result: {self.incident_id} ===",
            "",
            "Diagnosis:",
            f"  {self.diagnosis}",
            "",
            "Fix Plan:",
        ]
        for i, s in enumerate(self.fix_plan, 1):
            lines.append(f"  {i}. {s}")
        lines.append("")
        lines.append("Commands:")
        for c in self.commands:
            lines.append(f"  $ {c}")
        lines.append("")
        lines.append("Verification:")
        for v in self.verification:
            lines.append(f"  - {v}")
        lines.append("")
        lines.append("Rollback:")
        for r in self.rollback:
            lines.append(f"  - {r}")
        lines.append(f"\nCompleted in {self.duration_ms:.0f}ms")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

ModelFn = Callable[[str], str]
ModelLoader = Callable[[str, str], ModelFn]


class Orchestrator:
    """Coordinates the four-agent RCA pipeline.

    Each model argument is a callable `prompt -> text` — swap in an Ollama,
    vLLM, HF, or API client adapter. Use `from_role_defaults(loader)` to
    wire the approved defaults in one call.
    """

    def __init__(
        self,
        *,
        agent_1_model:    ModelFn | None = None,
        agent_2_model:    ModelFn | None = None,
        reconciler_model: ModelFn | None = None,
        validator_model:  ModelFn | None = None,
        parallel: bool = True,
    ):
        self.agent_1    = SolutionGeneratorAgent(name="agent_1", model=agent_1_model)
        self.agent_2    = SolutionGeneratorAgent(name="agent_2", model=agent_2_model)
        self.reconciler = ReconciliationAgent(model=reconciler_model)
        self.validator  = ValidationAgent(model=validator_model)
        self.parallel   = parallel

    # -- Factory helpers ---------------------------------------------------

    @classmethod
    def from_bootstrap(
        cls,
        model_loader: ModelLoader,
        *,
        model: str = "qwen3.5:9b",
        parallel: bool = True,
    ) -> "Orchestrator":
        """Wire a single model into all four agent slots.

        Use this to exercise the pipeline end-to-end before all five
        fine-tuned models are ready. Default is `qwen3.5:9b` — fast enough
        for dev iteration, capable at RCA/reconciliation/validation.

        Note: Agent 1 and Agent 2 will produce near-identical outputs in
        bootstrap mode (same model, same prompt). That's fine for plumbing
        tests; switch to `from_role_defaults` for real reasoning diversity.
        """
        call = model_loader("rca", model)
        return cls(
            agent_1_model    = call,
            agent_2_model    = call,
            reconciler_model = call,
            validator_model  = call,
            parallel         = parallel,
        )

    @classmethod
    def from_role_defaults(
        cls,
        model_loader: ModelLoader,
        *,
        agent_1: str | None = None,
        agent_2: str | None = None,
        reconciler: str | None = None,
        validator: str | None = None,
        parallel: bool = True,
    ) -> "Orchestrator":
        """Build an orchestrator with the approved default model per role.

        `model_loader(role, name)` is your model client adapter — it takes the
        role ("rca" / "executor" / "validator") and a model name
        (e.g. "qwen3.5:9b") and returns a callable `prompt -> text`.
        """
        names = {
            "agent_1":    agent_1    or DEFAULT_PIPELINE["agent_1"],
            "agent_2":    agent_2    or DEFAULT_PIPELINE["agent_2"],
            "reconciler": reconciler or DEFAULT_PIPELINE["reconciler"],
            "validator":  validator  or DEFAULT_PIPELINE["validator"],
        }
        role_of = {
            "agent_1": "rca", "agent_2": "rca",
            "reconciler": "executor", "validator": "validator",
        }
        for key, name in names.items():
            role = role_of[key]
            if name not in AGENT_ROLE_MODELS[role]:
                raise ValueError(
                    f"{name!r} is not an approved {role!r} model. "
                    f"Allowed: {AGENT_ROLE_MODELS[role]}"
                )

        return cls(
            agent_1_model    = model_loader("rca",       names["agent_1"]),
            agent_2_model    = model_loader("rca",       names["agent_2"]),
            reconciler_model = model_loader("executor",  names["reconciler"]),
            validator_model  = model_loader("validator", names["validator"]),
            parallel         = parallel,
        )

    # -- Pipeline ----------------------------------------------------------

    def analyze(self, incident: dict[str, Any]) -> StructuredRCAResult:
        """Run the full pipeline on one incident from k8s_combined_incidents.jsonl."""
        start = time.time()
        incident_id = (
            incident.get("id")
            or incident.get("pod_name")
            or incident.get("scenario_id", "unknown")
        )

        # --- Step 1: Agent 1 + Agent 2 generate candidate solutions ---
        if self.parallel:
            agent_1_result, agent_2_result = self._run_generators_parallel(incident)
        else:
            agent_1_result = self.agent_1.run(incident)
            agent_2_result = self.agent_2.run(incident)

        sol_1 = agent_1_result.findings
        sol_2 = agent_2_result.findings

        # --- Step 2: Reconciliation — pick/merge, produce fix plan + commands ---
        incident["_agent_1_solution"] = sol_1
        incident["_agent_2_solution"] = sol_2
        reconciler_result = self.reconciler.run(incident)
        reconciled = reconciler_result.findings

        # --- Step 3: Validation — verification + rollback ---
        incident["_reconciled_solution"] = reconciled
        validator_result = self.validator.run(incident)
        validated = validator_result.findings

        # --- Clean up injected keys ---
        for k in ("_agent_1_solution", "_agent_2_solution", "_reconciled_solution"):
            incident.pop(k, None)

        duration_ms = (time.time() - start) * 1000

        return StructuredRCAResult(
            incident_id          = incident_id,
            diagnosis            = reconciled.get("diagnosis", ""),
            fix_plan             = reconciled.get("fix_plan", []),
            commands             = reconciled.get("commands", []),
            verification         = validated.get("verification", []),
            rollback             = validated.get("rollback", []),
            agent_1_solution     = sol_1,
            agent_2_solution     = sol_2,
            reconciliation_notes = reconciled.get("notes", ""),
            agent_results        = {
                "agent_1":    agent_1_result,
                "agent_2":    agent_2_result,
                "reconciler": reconciler_result,
                "validator":  validator_result,
            },
            duration_ms          = duration_ms,
        )

    def analyze_batch(
        self,
        incidents: list[dict[str, Any]],
        max_workers: int = 4,
    ) -> list[StructuredRCAResult]:
        """Analyze multiple incidents in parallel (each incident still runs its
        internal two RCA agents in parallel when self.parallel is True)."""
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            return list(pool.map(self.analyze, incidents))

    # -- Internals ---------------------------------------------------------

    def _run_generators_parallel(
        self, incident: dict[str, Any]
    ) -> tuple[AgentResult, AgentResult]:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(self.agent_1.run, incident)
            f2 = pool.submit(self.agent_2.run, incident)
            return f1.result(), f2.result()
