"""Orchestrator — multi-agent RCA pipeline.

Pattern: Blackboard (shared memory bus) + Contract Net Lite (agents
bid for incidents) + a centralized protocol runner that advances the
rounds and bundles the result. The four classical stages still happen
under the hood:

    Incident
        │
        ▼
    Triage          → posts a "handles" tag for routing
        │
        ▼
    Bidding round   → eligible RCA agents post confidence scores
        │
        ▼
    Top-2 dispatch  → the two best bidders run in parallel
        │
        ├──► Agent A diagnosis  ─┐
        └──► Agent B diagnosis  ─┤  (low similarity → conflict signal)
                         │
                         ▼
        Decision / Reconciliation Agent  → diagnosis + fix_plan + commands
                         │
                         ▼
              Validation Agent           → verification + rollback
                         │
                         ▼
             Structured RCA Result
        (incl. trace = full Blackboard log)

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

Public surface unchanged: callers still build the Orchestrator with the
same constructor or factories and call analyze(incident) the same way.
The Blackboard / Registry / Triage live behind that surface.

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

from agents.base_agent import AgentResult, BaseAgent
from agents.blackboard import Blackboard, Message, Topics
from agents.registry import AgentRegistry, Capability, RegisteredAgent
from agents.solution_generator_agent import SolutionGeneratorAgent
from agents.reconciliation_agent import ReconciliationAgent
from agents.validation_agent import ValidationAgent
from agents.triage_agent import TriageAgent


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

    `trace` is the chronological audit log of every Blackboard message
    written during analyze(). Useful for demos and debugging; safe to
    ignore or strip for production callers that don't need it.
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
    trace: list[dict[str, Any]] = field(default_factory=list)

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
            "trace":                self.trace,
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
    """Coordinates the multi-agent RCA pipeline via a Blackboard + Contract Net.

    The default cast (agent_1, agent_2, reconciler, validator) is registered
    automatically. Specialists can be added via add_specialist().

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
        self.triage     = TriageAgent()

        # Default registry. RCA generators bid on every incident (handles="*").
        # Add specialists with add_specialist() to make bidding non-trivial.
        self.registry = AgentRegistry()
        self.registry.register(self.agent_1, Capability(role="rca", handles={"*"}, cost=1.0))
        self.registry.register(self.agent_2, Capability(role="rca", handles={"*"}, cost=1.0))
        self.registry.register(self.reconciler, Capability(role="executor", handles={"*"}))
        self.registry.register(self.validator, Capability(role="validator", handles={"*"}))
        self.registry.register(self.triage, Capability(role="triage", handles={"*"}, cost=0.0))

        self.parallel   = parallel
        self.agent_timeout_s = agent_timeout_s
        self.total_timeout_s = total_timeout_s

    # -- Specialist registration ------------------------------------------

    def add_specialist(self, agent: BaseAgent, capability: Capability) -> None:
        """Register an additional RCA specialist (e.g. networking, RBAC, storage).

        The agent's bid() method (or the capability's handles match against the
        incident's event_reason) determines whether it is selected for a given
        run. Specialists compete with the two default generators in the bidding
        round; selection picks the top-2 bidders for diversity.

        Example:
            from agents.solution_generator_agent import SolutionGeneratorAgent
            from agents.registry import Capability

            class NetworkingSpecialistAgent(SolutionGeneratorAgent):
                def bid(self, incident):
                    reason = (incident.get("event_reason") or "").lower()
                    msg    = (incident.get("event_message") or "").lower()
                    if "imagepull" in reason or "manifest unknown" in msg:
                        return 0.95
                    return 0.0

            orch.add_specialist(
                NetworkingSpecialistAgent(name="networking", model=ollama_call),
                Capability(role="rca",
                           handles={"ImagePullBackOff", "ErrImagePull",
                                    "DNSResolution", "FailedCreatePodSandBox"}),
            )
        """
        if capability.role != "rca":
            raise ValueError(
                f"add_specialist only accepts role='rca', got {capability.role!r}"
            )
        self.registry.register(agent, capability)

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
        configured pipeline timeout.
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

        # Per-request blackboard.
        bb = Blackboard()

        # Defensive copy — the caller's dict is untouched, and concurrent
        # invocations (e.g. analyze_batch) can't stomp each other's keys.
        work = copy.deepcopy(incident)

        incident_id = (
            work.get("id")
            or work.get("pod_name")
            or work.get("scenario_id", "unknown")
        )
        errors: list[str] = []

        # --- Phase 1: post the incident to the bus ---
        bb.write(Message(
            topic=Topics.INCIDENT, sender="orchestrator", payload=work,
        ))

        # --- Phase 1b: Triage decides the dispatch handles tag ---
        triage_result = self.triage.run(work)
        handles = triage_result.findings.get("handles") or "*"
        bb.write(Message(
            topic=Topics.BID_REQUEST, sender=self.triage.name,
            payload={"handles": handles, "incident_id": incident_id},
        ))

        # --- Phase 2: Contract Net — collect bids from RCA candidates ---
        candidates = self.registry.discover(role="rca", handles=handles)
        bids: list[tuple[RegisteredAgent, float]] = []
        for r in candidates:
            try:
                score = float(r.agent.bid(work))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{r.agent.name}: bid failed: {e!r}")
                score = 0.0
            score = max(0.0, min(1.0, score))
            bids.append((r, score))
            bb.write(Message(
                topic=Topics.BID, sender=r.agent.name,
                payload={"score": score, "handles": sorted(r.capability.handles)},
            ))

        # Filter by per-capability confidence floor; sort by (-score, cost).
        bids = [(r, s) for r, s in bids if s >= r.capability.confidence_floor]
        bids.sort(key=lambda x: (-x[1], x[0].capability.cost))

        # Top-2 for diversity. (If only one candidate, run the single one.)
        selected_agents = [r.agent for r, _ in bids[:2]]
        bb.write(Message(
            topic=Topics.DISPATCH, sender="orchestrator",
            payload={"selected": [a.name for a in selected_agents]},
        ))

        # --- Phase 3: dispatched RCA agents run (in parallel by default) ---
        if not selected_agents:
            errors.append("dispatch: no eligible RCA agents for this incident")
            diagnosis_results: list[AgentResult] = []
        else:
            diagnosis_results = self._run_agents(
                selected_agents, work, errors, deadline,
            )

        for r in diagnosis_results:
            bb.write(Message(
                topic=Topics.DIAGNOSIS, sender=r.agent_name,
                payload=r.findings if r.status == "success" else {},
            ))

        # Pad to length 2 to keep the result schema stable across selection sizes.
        while len(diagnosis_results) < 2:
            diagnosis_results.append(AgentResult(
                agent_name=f"slot_{len(diagnosis_results) + 1}",
                status="error",
                error="no agent dispatched to this slot",
            ))

        sol_1 = self._findings_or_empty(diagnosis_results[0])
        sol_2 = self._findings_or_empty(diagnosis_results[1])

        # --- Phase 3b: detect conflict (audit signal; reconciler runs anyway) ---
        if sol_1.get("diagnosis") and sol_2.get("diagnosis"):
            if self._diagnoses_disagree(sol_1, sol_2):
                bb.write(Message(
                    topic=Topics.CONFLICT, sender="orchestrator",
                    payload={
                        "diag_a": sol_1.get("diagnosis", ""),
                        "diag_b": sol_2.get("diagnosis", ""),
                    },
                ))

        # --- Phase 3c: Reconciliation — pick/merge, produce fix plan + commands ---
        work["_agent_1_solution"] = sol_1
        work["_agent_2_solution"] = sol_2
        reconciler_result = self._run_with_timeout(
            self.reconciler, work, "reconciler", errors, deadline,
        )
        reconciled = self._findings_or_empty(reconciler_result)
        bb.write(Message(
            topic=Topics.FIX_PLAN, sender=self.reconciler.name, payload=reconciled,
        ))

        # --- Phase 4: Validation — verification + rollback ---
        work["_reconciled_solution"] = reconciled
        validator_result = self._run_with_timeout(
            self.validator, work, "validator", errors, deadline,
        )
        validated = self._findings_or_empty(validator_result)
        bb.write(Message(
            topic=Topics.VALIDATION, sender=self.validator.name, payload=validated,
        ))

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
                "agent_1":    diagnosis_results[0],
                "agent_2":    diagnosis_results[1],
                "reconciler": reconciler_result,
                "validator":  validator_result,
            },
            duration_ms          = duration_ms,
            errors               = errors,
            trace                = [m.to_dict() for m in bb.trace()],
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

    def _run_agents(
        self,
        agents: list[BaseAgent],
        incident: dict[str, Any],
        errors: list[str],
        deadline: float | None,
    ) -> list[AgentResult]:
        """Run the given agents (parallel if self.parallel, else sequential).

        Each agent runs in a daemon thread with a queue, so a hung model
        cannot block the pipeline beyond the per-agent / total deadline.
        """
        if not self.parallel or len(agents) <= 1:
            return [
                self._run_with_timeout(a, incident, a.name, errors, deadline)
                for a in agents
            ]

        cap = self._agent_cap(deadline)
        if cap is not None and cap <= 0:
            msg = "generators: skipped — pipeline deadline exceeded"
            errors.append(msg)
            return [
                AgentResult(agent_name=a.name, status="error", error=msg)
                for a in agents
            ]

        queues = [queue.Queue(maxsize=1) for _ in agents]
        for a, q in zip(agents, queues):
            t = threading.Thread(
                target=self._run_into_queue,
                args=(a, incident, q),
                name=f"rca-{a.name}",
                daemon=True,
            )
            t.start()

        results: list[AgentResult] = []
        wait_start = time.time()
        for a, q in zip(agents, queues):
            elapsed = time.time() - wait_start
            remaining_cap = (
                max(cap - elapsed, 0.0) if cap is not None else None
            )
            results.append(self._await_queue(q, a.name, remaining_cap, errors))
        return results

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

    @staticmethod
    def _diagnoses_disagree(sol_1: dict[str, Any], sol_2: dict[str, Any]) -> bool:
        """Token-level Jaccard similarity test on the diagnosis prose.

        Used purely as an audit signal: when two diagnoses are sufficiently
        different we post a `conflict` message to the bus so the trace shows
        an explicit conflict-resolution event. The reconciler runs in either
        case — agreement or disagreement — to produce the final fix plan.
        """
        d1 = (sol_1.get("diagnosis", "") or "").lower().strip()
        d2 = (sol_2.get("diagnosis", "") or "").lower().strip()
        if not d1 or not d2:
            return False
        t1, t2 = set(d1.split()), set(d2.split())
        if not t1 or not t2:
            return False
        overlap = len(t1 & t2) / len(t1 | t2)
        return overlap < 0.5


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
