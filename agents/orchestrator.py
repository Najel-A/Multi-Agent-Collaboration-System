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

Safety contract:
    - The input incident dict is never mutated — analyze() works on a deep copy.
    - Per-agent and pipeline-wide timeouts protect against slow / hung models.
    - Agent failures are captured in result.errors; the pipeline degrades to
      partial output rather than raising.
    - Generated commands are advisory: result.approval_status starts as
      "pending" and must be flipped to "approved" before any caller executes
      them. analyze(approval_callback=...) provides an inline approval hook;
      execute_commands() is the only sanctioned executor and refuses unless
      the result has been approved.

Data source: data/02-raw/k8s_combined_incidents.jsonl (flat 20-field schema).
SFT data:    data/sft/{rca,executor,validator}_*.jsonl (see
             data/01-generation/generate_sft_by_role.py for prompt shapes).
"""

import copy
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

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

ApprovalStatus = Literal["pending", "approved", "rejected"]


@dataclass
class StructuredRCAResult:
    """The 'Structured RCA Result' box in the diagram.

    Commands are advisory until approval_status flips to "approved" via
    approve(). execute_commands() refuses to run while status is "pending"
    or "rejected" — the only sanctioned execution path goes through that gate.
    """
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
    errors: list[str] = field(default_factory=list)
    approval_status: ApprovalStatus = "pending"
    approver: str | None = None
    approval_note: str = ""

    def approve(self, approver: str, note: str = "") -> None:
        if not approver:
            raise ValueError("approver is required")
        self.approval_status = "approved"
        self.approver = approver
        self.approval_note = note

    def reject(self, approver: str, reason: str) -> None:
        if not approver:
            raise ValueError("approver is required")
        if not reason:
            raise ValueError("reason is required when rejecting")
        self.approval_status = "rejected"
        self.approver = approver
        self.approval_note = reason

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
            "errors":               self.errors,
            "approval_status":      self.approval_status,
            "approver":             self.approver,
            "approval_note":        self.approval_note,
        }

    def summary(self) -> str:
        approval = self.approval_status.upper()
        if self.approver:
            approval += f" by {self.approver}"
            if self.approval_note:
                approval += f" — {self.approval_note}"
        lines = [
            f"=== RCA Result: {self.incident_id} ===",
            f"Approval: {approval}",
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
        prefix = "  $ " if self.approval_status == "approved" else "  [PENDING] $ "
        for c in self.commands:
            lines.append(f"{prefix}{c}")
        lines.append("")
        lines.append("Verification:")
        for v in self.verification:
            lines.append(f"  - {v}")
        lines.append("")
        lines.append("Rollback:")
        for r in self.rollback:
            lines.append(f"  - {r}")
        if self.errors:
            lines.append("")
            lines.append("Errors:")
            for e in self.errors:
                lines.append(f"  ! {e}")
        lines.append(f"\nCompleted in {self.duration_ms:.0f}ms")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

ModelFn = Callable[[str], str]
ModelLoader = Callable[[str, str], ModelFn]
ApprovalCallback = Callable[[StructuredRCAResult], bool]
CommandRunner = Callable[[str], str]


class CommandsNotApprovedError(PermissionError):
    """Raised by execute_commands() when the result has not been approved."""


class Orchestrator:
    """Coordinates the four-agent RCA pipeline.

    Each model argument is a callable `prompt -> text` — swap in an Ollama,
    vLLM, HF, or API client adapter. Use `from_role_defaults(loader)` to
    wire the approved defaults in one call.

    Timeouts:
        agent_timeout_s — max wall-time per agent call (default 60s).
        total_timeout_s — max wall-time for the whole pipeline (default 300s).
        Both are advisory caps. Python cannot force-kill threads, so the
        underlying model client must also enforce its own socket / HTTP
        timeout — the bundled loaders do (120s default).
    """

    def __init__(
        self,
        *,
        agent_1_model:    ModelFn | None = None,
        agent_2_model:    ModelFn | None = None,
        reconciler_model: ModelFn | None = None,
        validator_model:  ModelFn | None = None,
        parallel: bool = True,
        agent_timeout_s: float | None = 60.0,
        total_timeout_s: float | None = 300.0,
    ):
        self.agent_1    = SolutionGeneratorAgent(name="agent_1", model=agent_1_model)
        self.agent_2    = SolutionGeneratorAgent(name="agent_2", model=agent_2_model)
        self.reconciler = ReconciliationAgent(model=reconciler_model)
        self.validator  = ValidationAgent(model=validator_model)
        self.parallel   = parallel
        self.agent_timeout_s = agent_timeout_s
        self.total_timeout_s = total_timeout_s

    # -- Factory helpers ---------------------------------------------------

    @classmethod
    def from_bootstrap(
        cls,
        model_loader: ModelLoader,
        *,
        model: str = "qwen3.5:9b",
        parallel: bool = True,
        agent_timeout_s: float | None = 60.0,
        total_timeout_s: float | None = 300.0,
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
            agent_timeout_s  = agent_timeout_s,
            total_timeout_s  = total_timeout_s,
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
        agent_timeout_s: float | None = 60.0,
        total_timeout_s: float | None = 300.0,
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
            agent_timeout_s  = agent_timeout_s,
            total_timeout_s  = total_timeout_s,
        )

    # -- Pipeline ----------------------------------------------------------

    def analyze(
        self,
        incident: dict[str, Any],
        *,
        approval_callback: ApprovalCallback | None = None,
        total_timeout_s: float | None = None,
    ) -> StructuredRCAResult:
        """Run the full pipeline on one incident.

        The input `incident` dict is never mutated — the orchestrator
        operates on a deep copy. Agent failures and timeouts are captured
        in result.errors; the pipeline degrades to partial output rather
        than raising.

        approval_callback (optional): called with the produced result; if it
        returns truthy, the result is approved; otherwise it is rejected.
        Without a callback the result returns with approval_status="pending"
        and the caller MUST approve before any command is executed.

        total_timeout_s (optional): per-request override of the orchestrator's
        configured pipeline timeout. Lets API callers (e.g. the LSA-WebApp's
        `max_time` field) supply a deadline for this single invocation.
        """
        start = time.time()
        effective_total = (
            total_timeout_s
            if total_timeout_s is not None
            else self.total_timeout_s
        )
        deadline = (
            start + effective_total
            if effective_total is not None
            else None
        )

        # Defensive copy — the caller's dict is untouched, and concurrent
        # invocations (e.g. analyze_batch) can't stomp each other's keys.
        work = copy.deepcopy(incident)

        incident_id = (
            work.get("id")
            or work.get("pod_name")
            or work.get("scenario_id", "unknown")
        )
        errors: list[str] = []

        # --- Step 1: Agent 1 + Agent 2 generate candidate solutions ---
        agent_1_result, agent_2_result = self._run_generators(
            work, errors, deadline,
        )
        sol_1 = self._findings_or_empty(agent_1_result)
        sol_2 = self._findings_or_empty(agent_2_result)

        # --- Step 2: Reconciliation — pick/merge, produce fix plan + commands ---
        work["_agent_1_solution"] = sol_1
        work["_agent_2_solution"] = sol_2
        reconciler_result = self._run_with_timeout(
            self.reconciler, work, "reconciler", errors, deadline,
        )
        reconciled = self._findings_or_empty(reconciler_result)

        # --- Step 3: Validation — verification + rollback ---
        work["_reconciled_solution"] = reconciled
        validator_result = self._run_with_timeout(
            self.validator, work, "validator", errors, deadline,
        )
        validated = self._findings_or_empty(validator_result)

        duration_ms = (time.time() - start) * 1000

        result = StructuredRCAResult(
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
            errors               = errors,
        )

        if approval_callback is not None and result.commands:
            try:
                decision = bool(approval_callback(result))
            except Exception as e:
                result.errors.append(f"approval_callback raised: {e!r}")
                decision = False
            if decision:
                result.approve("approval_callback")
            else:
                result.reject("approval_callback", "callback returned falsy")

        return result

    def analyze_batch(
        self,
        incidents: list[dict[str, Any]],
        max_workers: int = 4,
    ) -> list[StructuredRCAResult]:
        """Analyze multiple incidents in parallel.

        Each result returns with approval_status="pending" — batch mode does
        not invoke approval callbacks. Approve interactively per-result
        before executing any commands.
        """
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            return list(pool.map(self.analyze, incidents))

    # -- Internals ---------------------------------------------------------

    def _run_generators(
        self,
        incident: dict[str, Any],
        errors: list[str],
        deadline: float | None,
    ) -> tuple[AgentResult, AgentResult]:
        if not self.parallel:
            r1 = self._run_with_timeout(
                self.agent_1, incident, "agent_1", errors, deadline,
            )
            r2 = self._run_with_timeout(
                self.agent_2, incident, "agent_2", errors, deadline,
            )
            return r1, r2

        cap = self._agent_cap(deadline)
        if cap is not None and cap <= 0:
            msg = "generators: skipped — pipeline deadline exceeded"
            errors.append(msg)
            return (
                AgentResult(agent_name="agent_1", status="error", error=msg),
                AgentResult(agent_name="agent_2", status="error", error=msg),
            )

        q1: "queue.Queue[Any]" = queue.Queue(maxsize=1)
        q2: "queue.Queue[Any]" = queue.Queue(maxsize=1)
        t1 = threading.Thread(
            target=self._run_into_queue,
            args=(self.agent_1, incident, q1),
            name="rca-agent_1",
            daemon=True,
        )
        t2 = threading.Thread(
            target=self._run_into_queue,
            args=(self.agent_2, incident, q2),
            name="rca-agent_2",
            daemon=True,
        )
        t1.start()
        t2.start()

        wait_start = time.time()
        r1 = self._await_queue(q1, "agent_1", cap, errors)
        elapsed = time.time() - wait_start
        r2_cap = max(cap - elapsed, 0.0) if cap is not None else None
        r2 = self._await_queue(q2, "agent_2", r2_cap, errors)
        return r1, r2

    def _run_with_timeout(
        self,
        agent: Any,
        incident: dict[str, Any],
        label: str,
        errors: list[str],
        deadline: float | None,
    ) -> AgentResult:
        cap = self._agent_cap(deadline)
        if cap is not None and cap <= 0:
            msg = f"{label}: skipped — pipeline deadline exceeded"
            errors.append(msg)
            return AgentResult(agent_name=label, status="error", error=msg)
        q: "queue.Queue[Any]" = queue.Queue(maxsize=1)
        thread = threading.Thread(
            target=self._run_into_queue,
            args=(agent, incident, q),
            name=f"rca-{label}",
            daemon=True,
        )
        thread.start()
        return self._await_queue(q, label, cap, errors)

    @staticmethod
    def _run_into_queue(
        agent: Any,
        incident: dict[str, Any],
        q: "queue.Queue[Any]",
    ) -> None:
        try:
            q.put(agent.run(incident))
        except Exception as e:  # noqa: BLE001 — boundary; surfaced via queue
            q.put(e)

    @staticmethod
    def _await_queue(
        q: "queue.Queue[Any]",
        label: str,
        timeout: float | None,
        errors: list[str],
    ) -> AgentResult:
        try:
            outcome = q.get(timeout=timeout)
        except queue.Empty:
            msg = f"{label}: timed out after {timeout}s"
            errors.append(msg)
            return AgentResult(agent_name=label, status="error", error=msg)
        if isinstance(outcome, BaseException):
            msg = f"{label}: {outcome!r}"
            errors.append(msg)
            return AgentResult(agent_name=label, status="error", error=msg)
        if outcome.status == "error" and outcome.error:
            errors.append(f"{label}: {outcome.error}")
        return outcome

    def _agent_cap(self, deadline: float | None) -> float | None:
        """Effective cap for one agent call: min(agent_timeout_s, time-remaining)."""
        remaining = (
            max(deadline - time.time(), 0.0)
            if deadline is not None
            else None
        )
        if self.agent_timeout_s is None:
            return remaining
        if remaining is None:
            return self.agent_timeout_s
        return min(self.agent_timeout_s, remaining)

    @staticmethod
    def _findings_or_empty(result: AgentResult) -> dict[str, Any]:
        if result.status == "success" and result.findings:
            return result.findings
        return {}


# ---------------------------------------------------------------------------
# Approval gate for command execution
# ---------------------------------------------------------------------------

def execute_commands(
    result: StructuredRCAResult,
    runner: CommandRunner,
) -> list[str]:
    """Execute approved commands one-by-one. The only sanctioned executor.

    Refuses to run unless result.approval_status == "approved" — raises
    CommandsNotApprovedError otherwise. The caller must have set approval
    via result.approve(approver, note) or via an approval_callback in
    Orchestrator.analyze().

    `runner` is a callable `command -> output`. The runner — not this
    function — is responsible for whatever safety the environment requires
    (namespace allow-list, dry-run mode, kubectl context pinning, etc.).
    """
    if result.approval_status != "approved":
        raise CommandsNotApprovedError(
            f"refusing to execute commands for {result.incident_id!r}: "
            f"approval_status={result.approval_status!r}"
        )
    outputs: list[str] = []
    for cmd in result.commands:
        outputs.append(runner(cmd))
    return outputs


# ---------------------------------------------------------------------------
# Section-formatted text rendering
# ---------------------------------------------------------------------------

def format_as_sections(result: StructuredRCAResult) -> str:
    """Render a StructuredRCAResult into the five-header text format expected
    by frontend RCA parsers (e.g. LSA-WebApp's parseRcaResponse). Headers are
    plain text on their own line — no markdown prefix required:

        Diagnosis
        Step-by-Step Fix Plan
        Concrete Actions or Commands to Apply the Fix
        Verification Steps to Confirm the Fix Worked
        Rollback Guidance if the Fix Causes Issues

    Commands are prefixed with `[PENDING APPROVAL]` until result.approval_status
    flips to "approved" — the gate stays visible to the operator reading the
    rendered output.
    """
    lines: list[str] = []

    lines.append("Diagnosis")
    lines.append(result.diagnosis or "(no diagnosis)")
    lines.append("")

    lines.append("Step-by-Step Fix Plan")
    if result.fix_plan:
        for i, step in enumerate(result.fix_plan, 1):
            lines.append(f"{i}. {step}")
    else:
        lines.append("(no fix plan)")
    lines.append("")

    lines.append("Concrete Actions or Commands to Apply the Fix")
    cmd_prefix = "" if result.approval_status == "approved" else "[PENDING APPROVAL] "
    if result.commands:
        for cmd in result.commands:
            lines.append(f"- {cmd_prefix}{cmd}")
    else:
        lines.append("(no commands)")
    lines.append("")

    lines.append("Verification Steps to Confirm the Fix Worked")
    if result.verification:
        for step in result.verification:
            lines.append(f"- {step}")
    else:
        lines.append("(no verification steps)")
    lines.append("")

    lines.append("Rollback Guidance if the Fix Causes Issues")
    if result.rollback:
        for step in result.rollback:
            lines.append(f"- {step}")
    else:
        lines.append("(no rollback guidance)")

    return "\n".join(lines)
