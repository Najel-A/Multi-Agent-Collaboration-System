# End-to-End System Architecture

Full architecture from live k8s event ingestion through continuously retrained models, covering data ingestion, orchestration, agents, feedback loops, user feedback, the incident store, and the training data pipeline.

## Architecture diagram

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                               USER INTERFACE                                  ║
║                                                                               ║
║             api/dashboard.html      →       React UI (future)                 ║
║                                                                               ║
║   [paste/load incident]   [Analyze]   [👍 accept / 👎 reject / edit fix]     ║
╚══════════════════════════════════════╤════════════════════════════════════════╝
                                       │ HTTPS
                                       ▼
╔═══════════════════════════════════════════════════════════════════════════════╗
║                           BACKEND API  (FastAPI)                              ║
║                               api/main.py                                     ║
║                                                                               ║
║   GET  /incidents/samples   POST /analyze   POST /feedback   GET /health     ║
║   POST /incidents (ingest)  GET  /incidents/{id}/result                      ║
╚════════╤═════════════════════╤═══════════════════════╤════════════════════════╝
         │ ingest              │ analyze()             │ feedback
         │                     │                       │
   ┌─────▼────────────┐  ┌─────▼─────────────┐  ┌─────▼──────────────┐
   │ DATA INGESTION   │  │  ORCHESTRATOR     │  │  FEEDBACK HANDLER  │
   │ (to build)       │  │  agents/          │  │  (to build)        │
   │                  │  │  orchestrator.py  │  │                    │
   │ Sources:         │  │                   │  │  Writes labeled    │
   │ - k8s API watch  │  │  analyze():       │  │  examples to store:│
   │ - events webhook │  │  ┌─ Agent 1 ──┐   │  │  {incident, result,│
   │ - alertmanager   │  │  ├─ Agent 2 ──┤   │  │   user_verdict,    │
   │ - JSONL replay   │  │  (parallel)   │   │  │   corrected_fix}   │
   │                  │  │      ▼        │   │  │                    │
   │ Normalizer →     │  │   Reconciler  │   │  │  Triggers internal │
   │ flat incident    │  │      ▼        │   │  │  retry when user   │
   │ dict (same shape │  │   Validator   │   │  │  flags "fix wrong" │
   │ as k8s_combined_ │  │      ▼        │   │  │                    │
   │ incidents.jsonl) │  │  StructuredRCA│   │  │                    │
   │                  │  │   Result      │   │  │                    │
   └────────┬─────────┘  └──────┬────────┘  └───────┬────────────────┘
            │                   │                   │
            └───────────────────┴───────────────────┘
                                │
                                ▼
╔═══════════════════════════════════════════════════════════════════════════════╗
║                      INCIDENT STORE  (Data Layer)                             ║
║                      Postgres (recommended) / S3 / JSONL                      ║
║                                                                               ║
║   ┌─ incidents ────────────────┐  ┌─ rca_results ──────────────────┐         ║
║   │ id, pod_name, scenario_id, │  │ incident_id, diagnosis, fix,   │         ║
║   │ namespace, event_msg,      │  │ commands, verif, rollback,     │         ║
║   │ pod_describe, pod_logs,    │  │ agent_1_diag, agent_2_diag,    │         ║
║   │ ingested_at, source        │  │ reconciliation_notes,          │         ║
║   └────────────────────────────┘  │ duration_ms, model_versions    │         ║
║                                   └────────────────────────────────┘         ║
║   ┌─ user_feedback ────────────┐  ┌─ training_examples ────────────┐         ║
║   │ result_id, verdict,        │  │ role, prompt, completion,      │         ║
║   │ correction_text, reporter, │  │ source_incident_id, approved,  │         ║
║   │ timestamp                  │  │ created_at                     │         ║
║   └────────────────────────────┘  └────────────────────────────────┘         ║
╚═════════════╤══════════════════════╤══════════════════════════════════╤══════╝
              │                      │                                  ▲
              │ metrics              │ telemetry                        │ curated
              ▼                      ▼                                  │ examples
      ┌──────────────┐     ┌──────────────────┐                        │
      │ DASHBOARDS   │     │  OBSERVABILITY   │                        │
      │              │     │                  │                        │
      │ - rejection  │     │ - per-agent      │                        │
      │   rate       │     │   latency        │                        │
      │ - agent      │     │ - model version  │                        │
      │   disagree   │     │   distribution   │                        │
      │ - accuracy   │     │ - traces         │                        │
      │   vs human   │     │   (LangSmith /   │                        │
      └──────────────┘     │    OTel)         │                        │
                           └──────────────────┘                        │
                                                                       │
╔══════════════════════════════════════════════════════════════════════╪════╗
║                 TRAINING DATA PIPELINE  (nightly/weekly)             │    ║
║                                                                      │    ║
║   1. Curate: query incident store for (incident + approved result)   │    ║
║      pairs  where user_feedback.verdict = "accepted"                 │    ║
║                                                                      │    ║
║   2. Split by role → separate prompt/completion pairs for:           │    ║
║      data/sft/rca_*.jsonl        ← (incident, diagnosis)   ──────────┘    ║
║      data/sft/executor_*.jsonl   ← (incident + diag, commands)            ║
║      data/sft/validator_*.jsonl  ← (incident + fix, verif + rollback)     ║
║      (generate_sft_by_role.py already does this)                          ║
║                                                                           ║
║   3. Re-run SFT per role → new LoRA adapter per model                     ║
║                                                                           ║
║   4. Evaluate on held-out golden set:                                     ║
║      - scenario coverage: ≥95% of 19 failure types                        ║
║      - command correctness: scripted `kubectl dry-run` validation         ║
║      - human spot-check on 50 random outputs                              ║
║                                                                           ║
║   5. Promote: update model_registry → next `/analyze` call uses new       ║
║      adapter. Roll back on regression.                                    ║
║                                                                           ║
║   model_registry → agents/model_loaders.py (new Ollama tag / vLLM path)  ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

---

## Layer-by-layer breakdown

### 1. Data Ingestion *(to build)*

**Purpose:** normalize whatever the source gives you (live events, replay, webhooks) into the flat incident dict the orchestrator expects.

**Minimum viable:**

- A sidecar that watches `kubectl get events --watch -A`, filters for failures (`Warning Failed`, `BackOff`, etc.), fetches the associated `kubectl describe pod` and logs, POSTs to `/analyze`.
- ~50 lines of Python.

**Production:**

- k8s client-go watch on `/api/v1/events` per cluster
- Alertmanager webhook for Prometheus-driven incidents
- Dead-letter queue for partial incidents
- Deduplication window (same pod + event_reason within 60 s → one analysis)

**File:** new — `ingestion/k8s_watcher.py` or similar.

### 2. Backend API *(exists, extend for feedback)*

Already has `/analyze`, `/incidents/samples`, `/health`. Add:

- `POST /incidents` — ingestion endpoint (called by the watcher)
- `POST /feedback` — user verdict + corrections
- `GET /incidents/{id}/result` — fetch cached analysis

**File:** `api/main.py` (extend by ~80 lines)

### 3. Orchestrator *(exists)*

Unchanged. One extension for the internal feedback loop:

```python
def analyze(self, incident, max_retries=2):
    for attempt in range(max_retries + 1):
        sol_1, sol_2 = self._run_generators_parallel(incident)
        incident["_agent_1_solution"] = sol_1
        incident["_agent_2_solution"] = sol_2
        reconciled = self.reconciler.run(incident).findings
        incident["_reconciled_solution"] = reconciled
        validated = self.validator.run(incident).findings
        if validated.get("approved", True) or attempt == max_retries:
            break
        incident["_validator_feedback"] = validated.get("feedback", "")
        # next iteration: reconciler sees the feedback
```

~15 lines of changes. Requires the validator's prompt to emit an explicit `APPROVED` / `REJECTED` marker.

### 4. Agents *(exist)*

Unchanged. Model behind them is swappable via `agents/model_loaders.py` → points at model registry.

### 5. Feedback Loops *(two kinds)*

**Internal — validator rejection → retry.** Programmatic, bounded by `max_retries`. Gives the reconciler a second chance with the validator's feedback as extra context. Fast (just another pipeline run), no human in the loop.

**External — user verdict + corrections.** Dashboard shows 👍 / 👎 + an "Edit fix plan" button. User's response flows via `POST /feedback` to `user_feedback` table. Asynchronous. The training pipeline later consumes these as labels.

Without the external loop, you have no ground truth — the validator's `APPROVED` signal is model-generated, so training on it creates a feedback cartel (models approve their own mistakes). The external loop is what prevents drift.

### 6. Incident Store *(to build)*

**Minimum:**

- Append each analyzed incident + result as a JSONL row under `data/04-analyzed/`
- Separate file per day for partitioning
- ~30 lines; no new dependencies

**Recommended:**

- Postgres 15+ with a JSON column for the flexible parts of each row
- Tables: `incidents`, `rca_results`, `user_feedback`, `training_examples`
- Composite indexes on `(scenario_id, ingested_at)` and `(result_id, verdict)`
- `pg_cron` for the nightly training-data curation job

**Cloud / scale:**

- Stream incidents to S3 as parquet
- Athena / DuckDB for queries during training curation

### 7. Training Data Pipeline *(mostly exists, needs automation)*

You already have:

- `data/01-generation/generate_sft_by_role.py` → per-role JSONL
- Per-role SFT training notebooks

What's missing:

- A **scheduled job** that reads `training_examples` from the store, regenerates the per-role JSONL, and triggers the SFT notebooks
- A **golden eval set** (100–200 hand-labeled incidents) held out from training so you can measure regression
- A **model registry** — could be as simple as a JSON file mapping role → model tag → timestamp, read by `agents/model_loaders.py`

**Recommended cadence:**

- Curate nightly (cheap)
- Re-train weekly per role (~4 hours on a single A100 per role)
- Deploy only if golden-set accuracy doesn't regress

---

## What exists vs. what to build

| Layer | Status | File / Location |
|---|---|---|
| UI (basic) | Exists | `api/dashboard.html` |
| UI (React) | Future | — |
| Backend API `/analyze` | Exists | `api/main.py` |
| Backend API `/feedback` + `/incidents` | To build | `api/main.py` |
| Data ingestion from live cluster | To build | new `ingestion/` module |
| Incident JSONL replay | Exists | `data/02-raw/k8s_combined_incidents.jsonl` |
| Orchestrator core | Exists | `agents/orchestrator.py` |
| Internal feedback loop (retry on reject) | To build | +15 lines in `orchestrator.py` |
| 4 agents (RCA × 2 / Exec / Validator) | Exists | `agents/*_agent.py` |
| Model loaders (Ollama / vLLM) | Exists | `agents/model_loaders.py` |
| Incident store | To build | Postgres schema or JSONL partitions |
| User feedback capture | To build | dashboard + endpoint + table |
| SFT data generation | Exists | `data/01-generation/generate_sft_by_role.py` |
| SFT training notebooks | Exists | `agents/models/` |
| Training curation scheduler | To build | cron / Airflow / pg_cron |
| Golden eval set | To build | hand-labeled hold-out subset |
| Model registry | To build | JSON config or MLflow |
| Observability / traces | To build | LangSmith or OTel |

---

## The three interacting loops

1. **Synchronous inference loop** (milliseconds to seconds): user submits incident → orchestrator → structured result. That's what runs today.

2. **Internal retry loop** (seconds): validator rejects → reconciler re-plans with feedback. Opt-in, bounded. Reduces false fixes at the cost of 2× inference when it fires.

3. **Training feedback loop** (days to weeks): accumulated user verdicts → curated SFT data → re-fine-tuned adapter → swapped in via model registry. This is the loop that makes the system actually *improve* over time, not just operate.

Without loop 3, the system is static: inference without learning. Without loop 1 you have no product. Loop 2 is the one that's genuinely optional — it's a quality lever you pull when rejection rate is high enough to justify the extra compute.

---

## What to build next, in order

1. **Incident store (JSONL partitions)** — 30 lines, unblocks everything else
2. **`POST /feedback` + dashboard 👍 / 👎** — lets you start collecting ground truth *now*, even before the rest is built
3. **Internal retry loop** — small code change, big quality gain on reject-prone scenarios
4. **Nightly curation job** — takes curated feedback → regenerates per-role SFT JSONL
5. **Golden eval set** — label 200 incidents by hand, freeze; this is your regression firewall
6. **Model registry** — start as a JSON file; upgrade to MLflow when you have >3 checkpoints per role
7. **Live k8s watcher** — last, because replay from JSONL is enough for development

---

## Related reading

- `docs/how_the_orchestrator_works.md` — design & mental model of the coordinator
- `docs/orchestrator_scenarios.md` — three end-to-end scenario walkthroughs
- `docs/running_end_to_end.md` — how to run the current pipeline via the FastAPI service
- `tests/test_orchestrator.py` — executable specification of pipeline behavior
