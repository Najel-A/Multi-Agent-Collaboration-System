# NexusTrace — async multi-agent inference service

Single FastAPI app that runs a four-agent Kubernetes incident-analysis
pipeline (`agent_1` ∥ `agent_2` → `reconciler` → `validator`) using
`asyncio` for parallelism instead of threads + Redis pub/sub. Each agent
runs in **stub mode** (canned text, used by tests and demos without a
model server) or **real-model mode** (live LLM calls) depending on
whether its `*_URL` env var is set.

## ⚠️ Directory shadowing

This folder is named `fastapi/` to match the project spec, which collides
with the PyPI `fastapi` package. To avoid shadowing:

- There is **no** `__init__.py` at the top level of `fastapi/` — only
  inside the sub-packages (`agents/`, `schemas/`, `services/`, `tests/`).
- **Always run uvicorn and pytest from inside this directory.** Do not
  invoke `uvicorn fastapi.app:app` from the repo root — it will resolve
  `fastapi` to the installed library.

## Run — stub mode (no models needed)

```bash
cd fastapi
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8001 --reload
```

Port **8001** is intentional — the existing `api/main.py` inference service
already binds port 8000.

## Run — real-model mode (Ollama on macOS / Linux)

Verified working end-to-end with native Ollama base models — fast,
no GPU needed, all four agents reasoning over real evidence.

```bash
brew install ollama
ollama serve &
ollama pull qwen2.5:7b deepseek-r1:7b mistral:7b llama3.2:3b

cd fastapi
cp .env.example .env
set -a && source .env && set +a
uvicorn app:app --host 0.0.0.0 --port 8001 --reload
```

> **Full setup on any machine:** see [`RUNNING.md`](RUNNING.md) for the
> portable guide covering macOS, Linux, and Windows, with venv setup,
> troubleshooting, and Step 7 — wire real models with Ollama.

> **GPU host with the team's LoRA Docker images?** See
> [`RUNNING.md` Appendix A](RUNNING.md) for the recipe (adapter mounts,
> custom `/v1/rca/generate` API, GPU reservations).

## Endpoints

- `GET  /health` — liveness probe + agent roster
- `POST /analyze` — runs the four-agent pipeline on incident evidence;
  returns structured JSON with all four agent outputs and a merged
  `final_recommendation`

## Sample request

A real incident from `data/02-raw/k8s_combined_incidents.jsonl` (record 1
— `pvc_not_found_mountfail`) is bundled in `payload.json`. Use the `@`
syntax so you don't have to worry about JSON escaping in your shell:

```bash
curl -s -X POST http://localhost:8001/analyze \
  -H 'Content-Type: application/json' \
  -d @payload.json | python3 -m json.tool
```

## Five test scenarios in `payloads/`

Five additional incidents — one per failure category — are pre-bundled in
`payloads/`, drawn from the same JSONL. Swap the `@` filename to test each:

| File | Category | Scenario |
| ---- | -------- | -------- |
| `payloads/01_storage_pvc_not_found.json`     | Storage     | Missing PVC → FailedScheduling |
| `payloads/02_image_pull_bad_tag.json`        | Image pull  | Non-existent image tag |
| `payloads/03_runtime_oom_killed.json`        | Runtime     | Container OOM-killed (limit too low) |
| `payloads/04_config_missing_secret.json`     | Config      | Pod references a Secret that doesn't exist |
| `payloads/05_security_rbac_forbidden.json`   | Security    | ServiceAccount lacks RBAC permissions |

```bash
# Run any one:
curl -s -X POST http://localhost:8001/analyze \
  -H 'Content-Type: application/json' \
  -d @payloads/03_runtime_oom_killed.json | python3 -m json.tool

# Or run all five and grab just the diagnoses:
for f in payloads/*.json; do
  echo "=== $f ==="
  curl -s -X POST http://localhost:8001/analyze \
    -H 'Content-Type: application/json' \
    -d @"$f" | python3 -c "import sys,json; r=json.load(sys.stdin); print('chosen:', r['reconciler_output']['chosen_source']); print('diag:', r['final_recommendation']['diagnosis'][:120])"
done
```

In **stub mode**, all five return the same generic canned diagnosis
regardless of evidence — by design. The payloads exercise the
request/response pipeline on realistically-shaped K8s data.

In **real-model mode** (Ollama configured), each scenario gets an
evidence-aware diagnosis: PVC payloads name the missing volume, OOM
payloads identify the memory limit, RBAC payloads cite the missing
permissions, etc. Verified end-to-end on all five.

## Architecture

```
Incident Evidence
       │
       ▼
Shared Memory / Incident Blackboard
       │
   ┌───┴───┐
   ▼       ▼
Agent 1  Agent 2          ← parallel via asyncio.gather
   └───┬───┘
       ▼
Reconciliation Agent      ← reads both, picks higher-confidence diagnosis
       │
       ▼
Validation Agent          ← emits verification + rollback + safety analysis
       │
       ▼
AnalyzeResponse           ← incident_id + 4 typed outputs + final_recommendation
                            + requires_human_review
```

## Layout

```
fastapi/
  app.py                 FastAPI entry point (/health, /analyze)
  requirements.txt
  README.md
  RUNNING.md             portable setup guide (macOS / Linux / Windows)
  .env.example           Ollama-first config; copy to .env and source it
  payload.json           default sample (PVC scenario)
  agents/                one async coroutine per agent — stub or real model
    agent1.py            RCA generator A (qwen2.5:7b or stub)
    agent2.py            RCA generator B (deepseek-r1:7b or stub)
    reconciler.py        merges both diagnoses (mistral:7b or stub)
    validator.py         emits safety analysis (llama3.2:3b or stub)
  schemas/               Pydantic request/response models
    requests.py  responses.py
  services/              orchestrator + in-memory blackboard + HTTP client
    orchestrator.py  memory.py  model_client.py
  tests/                 pytest sanity tests (13 tests, ~3.5s)
    test_sanity.py
  payloads/              five themed K8s scenarios from the dataset
    01_storage_pvc_not_found.json
    02_image_pull_bad_tag.json
    03_runtime_oom_killed.json
    04_config_missing_secret.json
    05_security_rbac_forbidden.json
```

## Status

| Step | Description                                          | Status |
| ---- | ---------------------------------------------------- | ------ |
| 1    | Directory scaffold                                   | done   |
| 2    | Pydantic schemas (requests / responses)              | done   |
| 3    | `IncidentBlackboard` with per-incident locks         | done   |
| 4    | Stub agent coroutines                                | done   |
| 5    | Async orchestrator (asyncio.gather + sequential)     | done   |
| 6    | `/analyze` wiring                                    | done   |
| 7    | `requirements.txt` + README + RUNNING.md             | done   |
| 8    | pytest sanity tests (13/13 passing in 3.5s)          | done   |
| 9    | Manual `curl` sanity check (5 K8s scenarios)         | done   |
| 10   | Real-model wiring (Ollama OpenAI-compat) + verified  | done   |
| 11   | LoRA Docker images on GPU host                       | deferred (RUNNING.md Appendix A) |
| 12   | Approval endpoint for "Final Approved Fix"           | deferred |
| 13   | Backend integration                                  | deferred (per spec) |

## Run the tests

```bash
cd fastapi
python3 -m pytest tests/ -v
```

Three equivalent ways:

```bash
python3 -m pytest tests/ -v                # via pytest, all test files
python3 -m pytest tests/test_sanity.py -v  # via pytest, just this file
python3 tests/test_sanity.py               # direct script, no pytest CLI
```

All run the same 13 tests in stub mode (~3.5s). Live real-model
verification happens manually via `curl @payloads/*.json` after
sourcing `.env` (see Step 7 of `RUNNING.md`).
