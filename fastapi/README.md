# NexusTrace — async sanity-check inference service

Single FastAPI app that runs the four-agent pipeline
(`agent_1` + `agent_2` → `reconciler` → `validator`) with `asyncio` instead of
threads + Redis pub/sub. Stub agents only — backend integration comes later.

## ⚠️ Directory shadowing

This folder is named `fastapi/` to match the project spec, which collides
with the PyPI `fastapi` package. To avoid shadowing:

- There is **no** `__init__.py` at the top level of `fastapi/` — only
  inside the sub-packages (`agents/`, `schemas/`, `services/`, `tests/`).
- **Always run uvicorn and pytest from inside this directory.** Do not
  invoke `uvicorn fastapi.app:app` from the repo root — it will resolve
  `fastapi` to the installed library.

## Run

```bash
cd fastapi
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8001 --reload
```

Port **8001** is intentional — the existing `api/main.py` inference service
already binds port 8000.

## Endpoints

- `GET  /health` — liveness + agent roster
- `POST /analyze` — runs the pipeline (stubbed; returns 501 until step 5/6)

## Sample request

A real incident from `data/02-raw/k8s_combined_incidents.jsonl` (record 1 —
`pvc_not_found_mountfail`) is bundled in `payload.json`. Use the `@` syntax
so you don't have to worry about JSON escaping in your shell:

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

> Heads-up: the agents are still stubs, so all five will return the same
> generic diagnosis regardless of the actual error. That's expected — the
> point of these payloads is to exercise the request/response pipeline on
> realistically-shaped evidence. Real diagnostic differentiation lands once
> stubs are swapped for real models.

## Layout

```
fastapi/
  app.py                 FastAPI entrypoint (/health, /analyze)
  requirements.txt
  README.md
  agents/                stub coroutines, one per agent
    agent1.py  agent2.py  reconciler.py  validator.py
  schemas/               Pydantic request/response models
    requests.py  responses.py
  services/              orchestrator + in-memory blackboard
    orchestrator.py  memory.py
  tests/                 pytest sanity tests
    test_sanity.py
  payload.json           default sample (PVC scenario)
  payloads/              five themed test scenarios
    01_storage_pvc_not_found.json
    02_image_pull_bad_tag.json
    03_runtime_oom_killed.json
    04_config_missing_secret.json
    05_security_rbac_forbidden.json
```

## Status

| Step | Description                              | Status |
| ---- | ---------------------------------------- | ------ |
| 1    | Directory scaffold                       | done   |
| 2    | Pydantic schemas (requests / responses)  | done   |
| 3    | `IncidentBlackboard` with per-id locks   | done   |
| 4    | Stub agent coroutines                    | done   |
| 5    | Async orchestrator (gather + sequential) | done   |
| 6    | `/analyze` wiring                        | done   |
| 7    | requirements + README                    | done   |
| 8    | pytest sanity tests                      | done   |
| 9    | Manual `curl` sanity check               | done   |

## Run the tests

```bash
cd fastapi
python3 -m pytest tests/ -v
```
```
