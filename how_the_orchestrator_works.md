# How the Orchestrator of Agents Works

The orchestrator is a **coordinator, not an agent itself** — it doesn't do any reasoning. Its only job is to:

1. Run the right agents in the right order with the right data
2. Move each agent's output to the next agent's input
3. Bundle the pieces into the final structured result

Think of it as a conductor: the musicians play, the conductor decides when.

---

## The mental model in one paragraph

An **incident** (a dict of fields from `k8s_combined_incidents.jsonl`) enters `Orchestrator.analyze()`. The orchestrator immediately deep-copies it so the caller's dict is never mutated. The copy fans out to **two RCA agents running in parallel** (different models for reasoning diversity). Their two candidate diagnoses fan in to a **single reconciliation agent** that picks or merges them and emits a fix plan plus commands. That reconciled output flows to a **single validation agent** which produces verification and rollback steps. The orchestrator bundles everything into a `StructuredRCAResult` — pending human approval before any command is executed — and returns it. No agent ever talks to another directly; they all talk to the orchestrator, which passes data through the work-copy dict.

---

## Safety contract

Four guarantees `analyze()` provides to its caller:

1. **Immutable input.** The orchestrator deep-copies the incident at the top of `analyze()`. Every scratch key (`_agent_1_solution`, etc.) is written on the copy. Concurrent calls in `analyze_batch()` cannot collide on a shared dict.
2. **Bounded wall-time.** Each agent has a per-call cap (`agent_timeout_s`, default 60s) and the whole pipeline has a deadline (`total_timeout_s`, default 300s; per-request override available). Hung models can't stall the request indefinitely.
3. **Graceful degradation.** Agent exceptions and timeouts never raise out of `analyze()`. They're captured in `result.errors`; the pipeline continues with whatever is available (one failed generator still lets the reconciler run).
4. **Approval gate on commands.** `result.approval_status` starts at `"pending"`. `execute_commands(result, runner)` refuses to run unless the result has been explicitly approved via `result.approve(approver)`. There is no other sanctioned executor.

---

## The four moving parts

| Piece | Type | Lives in |
|---|---|---|
| **The Orchestrator** | a plain Python class — no LLM | `agents/orchestrator.py` |
| **Solution Generators (×2)** | stateless classes wrapping a model callable | `agents/solution_generator_agent.py` |
| **Reconciliation Agent** | same pattern | `agents/reconciliation_agent.py` |
| **Validation Agent** | same pattern | `agents/validation_agent.py` |

Each agent is a class with one method — `run(incident) -> AgentResult`. The orchestrator is what composes them.

---

## How data flows between them

```
incident (caller's dict — read-only, never mutated)
    │
    │  analyze() does work = copy.deepcopy(incident)
    ▼
work
    ├── daemon thread: agent_1.run(work) ─> diagnosis_1 ─┐
    ├── daemon thread: agent_2.run(work) ─> diagnosis_2 ─┤  parallel
    │      cap = min(agent_timeout_s, deadline - now)    │
    │                                                    │
    │   orchestrator writes (on the copy):               │
    │     work["_agent_1_solution"] = …                  │
    │     work["_agent_2_solution"] = …                  │
    │                                                    ▼
    │   reconciler.run(work) ─> {diagnosis, fix_plan, commands, notes}
    │                                                    │
    │   orchestrator writes (on the copy):               │
    │     work["_reconciled_solution"] = …               │
    │                                                    ▼
    │   validator.run(work) ─> {verification, rollback}
    │                                                    │
    └────────────────────────────────────────────────────▼
            StructuredRCAResult(
              incident_id, diagnosis, fix_plan, commands,
              verification, rollback,
              errors=[…],                  ← any agent failures or timeouts
              approval_status="pending",   ← gate before execution
            )
```

Three things to notice:

1. **Scratch keys live on the copy.** The orchestrator never mutates the caller's incident. The `_agent_*_solution` keys are written and read on `work`, which is a `copy.deepcopy(incident)`.
2. **Each agent runs on a daemon thread with a queue.** The orchestrator submits work via `threading.Thread(daemon=True)` and waits with `queue.get(timeout=cap)`. If a model hangs, the thread is orphaned (it'll exit when the underlying HTTP call's own timeout fires) and the pipeline records a timeout and continues. `ThreadPoolExecutor` was deliberately avoided here — its `__exit__` blocks on hung tasks.
3. **The orchestrator never parses model output.** That's the agents' job. The orchestrator only knows the *shape* of each agent's output (e.g. "reconciler returns `{diagnosis, fix_plan, commands, notes}`"), not the model details.

---

## Why this shape instead of a single monolithic prompt

A one-shot prompt "diagnose and fix this incident" has three problems this architecture solves:

| Problem | How the orchestrator fixes it |
|---|---|
| **Self-confirmation bias** — a model that proposes a fix tends to validate its own fix | Validator runs as a *separate* model, doesn't see its own reasoning as input |
| **Single-point-of-failure reasoning** — if the model misreads the describe, the whole answer is wrong | Two RCA models in parallel; the reconciler compares them and records which was preferred |
| **Model capability mismatch** — one model isn't best at prose, code, and judgment | `qwen3.5:9b` (general) + `deepseek-r1:8b` (reasoning) → `devstral-small-2:24b` (code) → `qwen3.5:35b` (judgment). Each role gets a model tuned for it. |

---

## Where coordination actually happens

Open `agents/orchestrator.py` and the whole coordinator is one method, `analyze()`. Conceptually:

```python
def analyze(self, incident, *, approval_callback=None, total_timeout_s=None):
    start = time.time()
    effective_total = total_timeout_s or self.total_timeout_s
    deadline = (start + effective_total) if effective_total is not None else None

    # Defensive copy — caller's dict is never touched.
    work = copy.deepcopy(incident)
    errors: list[str] = []

    # Step 1: Fan out — two RCA models in parallel,
    # each capped at min(per-agent timeout, deadline - now).
    a1, a2 = self._run_generators(work, errors, deadline)
    sol_1 = self._findings_or_empty(a1)
    sol_2 = self._findings_or_empty(a2)

    # Step 2: Fan in — reconciler reads the two diagnoses + the incident.
    work["_agent_1_solution"] = sol_1
    work["_agent_2_solution"] = sol_2
    rec = self._run_with_timeout(self.reconciler, work, "reconciler", errors, deadline)
    reconciled = self._findings_or_empty(rec)

    # Step 3: Validator emits verification + rollback.
    work["_reconciled_solution"] = reconciled
    val = self._run_with_timeout(self.validator, work, "validator", errors, deadline)
    validated = self._findings_or_empty(val)

    # Bundle. approval_status starts at "pending".
    result = StructuredRCAResult(
        incident_id=...,
        diagnosis=reconciled.get("diagnosis", ""),
        fix_plan=reconciled.get("fix_plan", []),
        commands=reconciled.get("commands", []),
        verification=validated.get("verification", []),
        rollback=validated.get("rollback", []),
        errors=errors,
    )

    # Optional inline approval hook — flips to "approved" or "rejected".
    if approval_callback and result.commands:
        ...

    return result
```

The complexity lives in the agents (prompts, parsing) and in the models (the reasoning). The orchestrator is deliberately boring — easy to test, easy to swap backends, easy to add steps to.

---

## What makes it "multi-agent" vs. just "multiple model calls"

Three properties:

1. **Role specialization.** Each agent has a distinct purpose (diagnose / reconcile / validate) with its own prompt, its own parser, its own model choice. They're not interchangeable.

2. **Independent reasoning.** Agents 1 and 2 don't see each other's output — they analyze the incident independently. Their disagreements are *signal*, not noise.

3. **Arbitration.** The reconciler is a distinct agent whose only job is to compare and merge. That's what distinguishes "multi-agent" from "ensemble" — ensembles average outputs, multi-agent systems *reason about* them.

The orchestrator makes those three properties possible but doesn't embody any of them. It's the scaffolding. The intelligence is in the agents.

---

## Related reading

- `running_end_to_end.md` — how to run the inference service (`/analyze`, `/query`, `/health`, `/ready`)
- `tests/test_orchestrator.py` — executable specification of these properties as assertions
