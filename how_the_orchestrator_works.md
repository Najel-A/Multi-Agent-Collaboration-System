# How the Orchestrator of Agents Works

The orchestrator is a **coordinator, not an agent itself** — it doesn't do any reasoning. Its only job is to:

1. Run the right agents in the right order with the right data
2. Pass each agent's output as input to the next
3. Collect the pieces into the final structured result

Think of it as a conductor: the musicians play, the conductor decides when.

---

## The mental model in one paragraph

An **incident** (a dict of fields from `k8s_combined_incidents.jsonl`) enters the orchestrator. It fans out to **two RCA agents running in parallel** (different models for reasoning diversity). Their two candidate diagnoses then fan in to a **single reconciliation agent** that picks or merges them and emits a fix plan plus commands. That reconciled output flows to a **single validation agent** that produces verification and rollback steps. The orchestrator bundles everything into a `StructuredRCAResult` and returns it. No agent ever talks to another directly — they all talk to the orchestrator, which passes messages between them by mutating a shared dict.

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
incident (dict)
    │
    ├── orchestrator submits to ThreadPool  ─┐
    │                                         │ parallel
    ├── agent_1.run(incident) ─> diagnosis_1 ─┤
    └── agent_2.run(incident) ─> diagnosis_2 ─┘
                                         │
    orchestrator writes:                 │
      incident["_agent_1_solution"] = …  │ ← scratch keys
      incident["_agent_2_solution"] = …  │   on the shared dict
                                         ▼
    reconciler.run(incident) ─> {diagnosis, fix_plan, commands, notes}
                                         │
    orchestrator writes:                 │
      incident["_reconciled_solution"] = … │
                                         ▼
    validator.run(incident) ─> {verification, rollback}
                                         │
                                         ▼
    orchestrator cleans up injected keys, bundles the outputs into
    StructuredRCAResult, returns it.
```

Three things to notice:

1. **The agents share state via the incident dict.** Each agent reads from it; the orchestrator writes intermediate results into underscored "scratch" keys (`_agent_1_solution`, etc.) so the next agent can find them. At the end of `analyze()`, the orchestrator pops those keys back out — the caller's dict is left unchanged.

2. **The orchestrator never parses model output.** That's the agents' job. The orchestrator only orchestrates — it knows the *shape* of each agent's output (e.g. "reconciler returns `{diagnosis, fix_plan, commands, notes}`"), not the model details.

3. **Dependency is implicit in execution order.** `agents_1_2 → reconciler → validator` is encoded by the order of calls in `analyze()`. There's no DAG library, no graph compiler. If you wanted to add a step, you'd add 3-4 lines to `analyze()` and nothing else changes.

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

Open `agents/orchestrator.py` and the whole coordinator is ~40 lines in `analyze()`:

```python
def analyze(self, incident):
    # Step 1: Fan out — two RCA models in parallel
    sol_1, sol_2 = self._run_generators_parallel(incident)

    # Step 2: Fan in — reconciler sees both + incident
    incident["_agent_1_solution"] = sol_1
    incident["_agent_2_solution"] = sol_2
    reconciled = self.reconciler.run(incident).findings

    # Step 3: Validator sees the reconciled plan
    incident["_reconciled_solution"] = reconciled
    validated = self.validator.run(incident).findings

    # Clean up scratch keys; bundle result
    ...
    return StructuredRCAResult(
        diagnosis=...,
        fix_plan=...,
        commands=...,
        verification=...,
        rollback=...,
    )
```

That's the entire "orchestration". The complexity lives in the agents (prompts, parsing) and in the models (the reasoning). The orchestrator is deliberately boring — which is what makes it easy to test, swap backends, and add steps later.

---

## What makes it "multi-agent" vs. just "multiple model calls"

Three properties:

1. **Role specialization.** Each agent has a distinct purpose (diagnose / reconcile / validate) with its own prompt, its own parser, its own model choice. They're not interchangeable.

2. **Independent reasoning.** Agents 1 and 2 don't see each other's output — they analyze the incident independently. Their disagreements are *signal*, not noise.

3. **Arbitration.** The reconciler is a distinct agent whose only job is to compare and merge. That's what distinguishes "multi-agent" from "ensemble" — ensembles average outputs, multi-agent systems *reason about* them.

The orchestrator makes those three properties possible but doesn't embody any of them. It's the scaffolding. The intelligence is in the agents.

---

## Related reading

- `docs/orchestrator_scenarios.md` — three end-to-end walkthroughs showing this flow for concrete error cases (missing Secret, bad ConfigMap key, bad image tag)
- `docs/running_end_to_end.md` — how to actually run the pipeline via the FastAPI service and dashboard
- `tests/test_orchestrator.py` — executable specification of these properties as assertions
