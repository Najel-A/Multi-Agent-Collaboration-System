# Running NexusTrace `fastapi/` on Another System

A portable, step-by-step guide to install and run the async sanity-check
inference service on any Mac, Linux, or Windows machine. Beginner-friendly —
copy-paste each command in order.

## Prerequisites

| Requirement | Why | How to check |
| ----------- | --- | ------------ |
| **Python 3.10 or newer** | The code uses `from __future__ import annotations`, `match` syntax, modern type hints | `python3 --version` |
| **pip** (comes with Python 3.4+) | Installs the dependencies | `python3 -m pip --version` |
| **git** | Clone the repo | `git --version` |
| **~150 MB free disk** | For Python packages | n/a |
| **Open TCP port 8001** | Where the server listens | n/a |

If `python3 --version` shows anything below 3.10, install a newer version:

- **macOS** — `brew install python@3.12`
- **Ubuntu/Debian** — `sudo apt install python3.12 python3.12-venv`
- **Windows** — install from <https://www.python.org/downloads/> (check "Add to PATH" during install)

---

## Step 1 — Clone the repository

```bash
git clone https://github.com/Najel-A/Multi-Agent-Collaboration-System.git
cd Multi-Agent-Collaboration-System
```

If you already have the repo, just `cd` into it.

> **Branch note**: at time of writing, the `fastapi/` directory lives on
> `chelsea-dev-pipeline`. If your fresh clone is on `main`, switch first:
> `git checkout chelsea-dev-pipeline`.

---

## Step 2 — Create a virtual environment (recommended)

A virtualenv isolates this project's dependencies so they don't pollute your
system Python. Skip this if you don't mind installing globally.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Windows (Command Prompt)

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

After activating, your prompt should show `(.venv)` at the start. Every
command in the rest of this guide assumes the venv is active.

To leave the venv later: `deactivate`.

---

## Step 3 — Install dependencies

```bash
cd fastapi
python3 -m pip install -r requirements.txt
```

This installs:

| Package | Purpose |
| ------- | ------- |
| `fastapi` | Web framework |
| `uvicorn[standard]` | ASGI server |
| `pydantic` | Request/response validation |
| `httpx` | HTTP client (used by tests) |
| `pytest` + `pytest-asyncio` | Test runner |

Expected install time: 30–60 seconds on a typical broadband connection.

> **Windows note**: use `python` instead of `python3` if `python3` isn't on
> your PATH. Same for `pip` vs `python -m pip`.

---

## Step 4 — Run the server

**You must run uvicorn from inside the `fastapi/` folder.** The folder is
named `fastapi/` to match the project spec, which collides with the PyPI
`fastapi` library — running from anywhere else causes import errors.

### All operating systems

```bash
uvicorn app:app --host 0.0.0.0 --port 8001 --reload
```

Wait for the line:

```
INFO:     Application startup complete.
```

Leave this terminal open. The server is now listening on
`http://localhost:8001`.

> **Why `0.0.0.0` and not `127.0.0.1`?** `0.0.0.0` makes the server
> reachable from other devices on your LAN (useful for demos). For purely
> local testing, `127.0.0.1` is fine and slightly more secure.

> **Why port 8001?** This repo also has an older single-model service in
> `api/main.py` that uses port 8000. Running on 8001 avoids the collision.

> **Don't have `uvicorn` on PATH?** Use `python3 -m uvicorn ...`.

---

## Step 5 — Verify it works

Open a **second terminal**. Don't close the first one.

### Quick alive check

```bash
curl -s http://localhost:8001/health
```

Expected:

```json
{"status":"ok","service":"fastapi-sanity","agents":["agent_1","agent_2","reconciler","validator"]}
```

### Run the full pipeline on a real K8s incident

```bash
cd Multi-Agent-Collaboration-System/fastapi
curl -s -X POST http://localhost:8001/analyze \
  -H 'Content-Type: application/json' \
  -d @payload.json
```

You'll get a long JSON response with seven keys: `incident_id`,
`agent_1_output`, `agent_2_output`, `reconciler_output`,
`validation_output`, `final_recommendation`, `requires_human_review`.

If you want it pretty-printed:

```bash
curl -s -X POST http://localhost:8001/analyze \
  -H 'Content-Type: application/json' \
  -d @payload.json | python3 -m json.tool
```

### Try the auto-generated web UI

Open in any browser:

> <http://localhost:8001/docs>

You'll see Swagger UI. Click **POST /analyze**, then "Try it out", paste
some `evidence_text`, and "Execute". No curl needed.

### Run all five themed K8s scenarios in a loop

```bash
for f in payloads/*.json; do
  echo "=== $f ==="
  curl -s -X POST http://localhost:8001/analyze \
    -H 'Content-Type: application/json' \
    -d @"$f" \
    | python3 -c "import sys,json; r=json.load(sys.stdin); print('chosen:', r['reconciler_output']['chosen_source']); print('diag :', r['final_recommendation']['diagnosis'][:120])"
done
```

Five blocks, each summarized to a chosen source + first 120 chars of the
diagnosis. **Windows PowerShell users**: bash loops don't work — use the
test suite (Step 6) instead, or open one of the files in
`fastapi/payloads/` and curl them individually.

---

## Step 6 — Run the automated tests

Closes the loop on every priority in the spec. Doesn't need the server
running — pytest spins up an in-memory copy.

```bash
cd Multi-Agent-Collaboration-System/fastapi
python3 -m pytest tests/ -v
```

Expected:

```
============================== 13 passed in 3.49s ==============================
```

Three ways to run the same suite, all equivalent:

```bash
python3 -m pytest tests/ -v                # via pytest, all test files
python3 -m pytest tests/test_sanity.py -v  # via pytest, just this file
python3 tests/test_sanity.py               # direct script, no pytest CLI
```

---

## Step 7 (optional) — Wire real models in place of the stubs

By default every agent returns canned text. To swap any of them for a
real model running in Docker, set the corresponding `*_URL` environment
variable. Each agent independently checks its own env var on import and
either calls the model or stays on the stub — partial rollouts are fine.

### 7.1. Pull the four model images (Apple Silicon needs `--platform linux/amd64`)

```bash
for img in deveshs18/rca-lora-qwen \
           deveshs18/rca-lora-deepseek \
           mrunalikatta/executor-mistral-24b \
           mrunalikatta/validator-llama-3b; do
  docker pull --platform linux/amd64 "$img:latest"
done
```

> The four images are amd64-only; on M-series Macs Docker emulates via
> QEMU/Rosetta. Inference will be slow (30-90s/agent) but functional.
> See "Troubleshooting" below for native-ARM alternatives.

### 7.2. Inspect the images to learn the API contract

The images have no Docker Hub README. Before assuming the API, confirm
each one's exposed port and entrypoint:

```bash
for img in deveshs18/rca-lora-qwen \
           deveshs18/rca-lora-deepseek \
           mrunalikatta/executor-mistral-24b \
           mrunalikatta/validator-llama-3b; do
  echo "=== $img ==="
  docker inspect "$img:latest" | python3 -c "
import sys, json
d = json.load(sys.stdin)[0]['Config']
print('  Entrypoint:', d.get('Entrypoint'))
print('  Cmd       :', d.get('Cmd'))
print('  Exposed   :', list((d.get('ExposedPorts') or {}).keys()))
print('  Env       :', d.get('Env'))
"
done
```

If the actual exposed port is **not 8000**, edit the right side of each
`ports:` line in `docker-compose.yml` accordingly.

### 7.3. Bring up the four model services

```bash
cd /Users/chelseajaculina/GitHub/Multi-Agent-Collaboration-System
docker compose up -d
docker compose ps   # all four should be 'running'
```

This starts:

| Service | Image | Host port |
| ------- | ----- | --------- |
| `rca-qwen`     | deveshs18/rca-lora-qwen          | 11001 |
| `rca-deepseek` | deveshs18/rca-lora-deepseek      | 11002 |
| `executor`     | mrunalikatta/executor-mistral-24b | 11003 |
| `validator`    | mrunalikatta/validator-llama-3b  | 11004 |

### 7.4. Configure FastAPI to use them

```bash
cd fastapi
cp .env.example .env

# Load the env vars into the current shell
set -a && source .env && set +a

# Restart uvicorn with the env vars active
uvicorn app:app --host 0.0.0.0 --port 8001 --reload
```

The agents now log `agent_X real` in their `notes` field instead of
`agent_X stub`. Send any payload to confirm:

```bash
curl -s -X POST http://localhost:8001/analyze \
  -H 'Content-Type: application/json' \
  -d @payload.json | python3 -c "
import sys, json
r = json.load(sys.stdin)
for k in ('agent_1_output', 'agent_2_output', 'reconciler_output', 'validation_output'):
    notes = r[k].get('notes') or r[k].get('safety_notes', '')
    print(f'{k:25s}  {notes[:80]}')
"
```

If you see `[real]` or `agent_X real` in those notes, the model calls
landed. If you see `[real-error-fallback]`, the model server returned an
error — check `docker compose logs <service>`.

### 7.5. Roll back to stub mode

Either unset the env vars:

```bash
unset RCA_QWEN_URL RCA_DEEPSEEK_URL EXECUTOR_URL VALIDATOR_URL
```

…or stop the model containers (`docker compose down`). The agents
auto-fall-back to stubs whenever their `*_URL` is unreachable, but
unsetting the env var is the explicit way to force stub mode.

### Partial rollouts

You don't need all four URLs set. Set only `RCA_QWEN_URL` and the other
three agents stay on stubs. Useful for:

- A/B testing one model at a time
- Confirming the wiring works on the cheapest agent (validator-3b)
  before paying the latency cost of the 24B executor
- Hybrid setups where some agents are real and some are mocked for
  deterministic tests

---

## Stopping the server

Go back to the terminal running `uvicorn` and press **Ctrl+C**.

Nothing persists between runs — the incident blackboard is purely
in-memory. Restarting the server gives you a clean slate.

---

## Troubleshooting

| Error | Cause | Fix |
| ----- | ----- | --- |
| `command not found: python3` | Python isn't installed or isn't on PATH | Install Python 3.10+ (see Prerequisites) |
| `command not found: pip` | Same | Use `python3 -m pip` instead of `pip` |
| `command not found: uvicorn` | The package was installed but its binary isn't on PATH | Use `python3 -m uvicorn app:app --host 0.0.0.0 --port 8001 --reload` |
| `command not found: pytest` | Same | Use `python3 -m pytest tests/ -v` |
| `ImportError: cannot import name 'FastAPI' from 'fastapi'` | You ran uvicorn from outside the `fastapi/` folder, and the local folder shadowed the installed library | `cd` into `fastapi/` first |
| `ModuleNotFoundError: No module named 'app'` | Same as above | `cd fastapi/` first |
| `OSError: [Errno 48] Address already in use` (macOS/Linux) | Port 8001 is taken | Find the culprit with `lsof -i :8001` and kill it, or use `--port 8002` |
| `OSError: [WinError 10048]` (Windows) | Same | Use `--port 8002` |
| `SSL: CERTIFICATE_VERIFY_FAILED` during pip install | Corporate proxy or outdated certs | Run `python3 -m pip install --upgrade certifi` and retry |
| `pip: permission denied` | You installed without a venv and your user lacks write access | Either use a venv (Step 2) or add `--user` to the pip command |
| Tests pass but live server fails | The TestClient skips some uvicorn-specific behavior | Check the uvicorn terminal for tracebacks |
| All five payloads return identical generic diagnoses | **Expected** — agents are stubs | Real model integration is the next phase |

### Per-OS quick checks

#### macOS

```bash
which python3 && python3 --version
which uvicorn && uvicorn --version
lsof -i :8001
```

#### Linux

```bash
which python3 && python3 --version
which uvicorn && uvicorn --version
ss -ltnp | grep :8001    # or: netstat -ltnp | grep :8001
```

#### Windows (PowerShell)

```powershell
where.exe python
python --version
Get-Process -Id (Get-NetTCPConnection -LocalPort 8001).OwningProcess
```

---

## Optional — running on a custom port or host

```bash
# Bind only to localhost
uvicorn app:app --host 127.0.0.1 --port 8001

# Different port
uvicorn app:app --host 0.0.0.0 --port 9000

# Production-style: 4 workers, no auto-reload
uvicorn app:app --host 0.0.0.0 --port 8001 --workers 4
```

> **Multi-worker note**: with `--workers > 1`, each worker has its own
> in-process `IncidentBlackboard`. The same `incident_id` sent to two
> different workers won't share state. For the sanity-check phase, stick
> with the default single-worker setup.

---

## What this service does (brief)

A single FastAPI app that runs the four-agent NexusTrace pipeline:

```
Incident Evidence
       │
       ▼
Shared Blackboard
       │
   ┌───┴───┐
   ▼       ▼
Agent 1  Agent 2          ← parallel via asyncio.gather
   └───┬───┘
       ▼
Reconciliation Agent      ← picks higher-confidence diagnosis
       │
       ▼
Validation Agent          ← emits verification + rollback steps
       │
       ▼
Structured JSON response
```

Agents are currently **stubs** — they return canned text regardless of the
evidence. The pipeline mechanics are real and exercised; real model
reasoning is the next phase. See `README.md` for architecture detail.

---

## Where to go next

- `README.md` — architecture, design decisions, status table
- `tests/test_sanity.py` — 13 automated tests, the source of truth for the
  contract
- `payloads/` — five real K8s incidents from
  `data/02-raw/k8s_combined_incidents.jsonl`, one per failure category
- Swagger UI at <http://localhost:8001/docs> while the server is running
