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

## Step 7 (recommended for Mac) — Wire real models with Ollama

By default every agent returns canned text. To get real reasoning over
the evidence, point each agent at a model server. **The fastest, most
reliable path on a Mac is Ollama running natively** — no Docker, no GPU
emulation, no missing adapter files. Verified working end-to-end on all
4 agents and all 5 themed K8s scenarios.

### 7.1. Install and start Ollama

```bash
brew install ollama
```

> Don't have Homebrew? Get it from <https://brew.sh> first.

In a dedicated terminal (call it **Terminal A**):

```bash
ollama serve
```

You should see `Listening on 127.0.0.1:11434`. **Leave this terminal
running** — Ollama is your model server now.

### 7.2. Pull the four models (one-time, in a separate terminal)

```bash
ollama pull qwen2.5:7b      # for agent_1     (~4.7 GB)
ollama pull deepseek-r1:7b  # for agent_2     (~4.7 GB)
ollama pull mistral:7b      # for reconciler  (~4.4 GB)
ollama pull llama3.2:3b     # for validator   (~2.0 GB)
```

> Total ~16 GB download, 1-3 minutes per model on broadband.

Verify they're available:

```bash
ollama list
```

Smoke-test the smallest one to make sure Ollama's API works:

```bash
curl -s http://localhost:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"llama3.2:3b","messages":[{"role":"user","content":"Say hi in 5 words"}],"max_tokens":20}' \
  | python3 -m json.tool
```

You should get a `choices[0].message.content` field with text. If you
get `{"error":...,"model 'llama3.2:3b' not found"}`, the pull didn't
finish — re-run `ollama pull llama3.2:3b`.

### 7.3. Configure FastAPI to use Ollama

```bash
cd fastapi
cp .env.example .env
```

The committed `.env.example` already points all four agents at
`http://localhost:11434` with the right Ollama model names — no editing
needed.

### 7.4. Start the FastAPI server with env vars active

```bash
set -a && source .env && set +a
uvicorn app:app --host 0.0.0.0 --port 8001 --reload
```

> **Order matters**: agents read the env vars on import, so the `set -a
> / source / set +a` block has to run **before** uvicorn launches in the
> same shell. If you `source` and then open a new terminal, the env vars
> are gone.

Wait for `Application startup complete.`

### 7.5. Verify real models are running

In a third terminal:

```bash
cd fastapi
curl -s -X POST http://localhost:8001/analyze \
  -H 'Content-Type: application/json' \
  -d @payloads/03_runtime_oom_killed.json \
  | python3 -m json.tool
```

> **First call**: 30-90 seconds while Ollama loads each model into RAM.
> **Subsequent calls**: 10-25 seconds total for all four agents.

Check the `notes` fields:

| Before (stub mode) | After (real mode) |
| ------------------- | ----------------- |
| `"agent_1 stub; ..."` | `"agent_1 real; ..."` |
| `"agent_2 stub; ..."` | `"agent_2 real; ..."` |
| `"[stub] selected agent_1..."` | `"[real] selected agent_1..."` |
| `"[stub] Stub validator..."` | `"[real] ..."` |

If you see `agent_X real-empty-fallback`, the model returned an empty
response (usually because a reasoning model spent all its tokens on
`<think>` blocks before answering). The `model_client.py` strips those
blocks automatically; if it still happens, bump `MODEL_TIMEOUT_S` and
retry.

### 7.6. Run all five K8s scenarios end-to-end

```bash
for f in payloads/*.json; do
  echo "=== $(basename $f) ==="
  curl -s -X POST http://localhost:8001/analyze \
    -H 'Content-Type: application/json' -d @"$f" \
    | python3 -c "
import sys, json
r = json.load(sys.stdin)
print('a1 :', r['agent_1_output']['notes'][:35])
print('a2 :', r['agent_2_output']['notes'][:35])
print('rec:', r['reconciler_output']['notes'][:35])
print('diag:', r['final_recommendation']['diagnosis'][:120])
print()
"
done
```

Expected: ~3-5 minutes total, all four agents reporting `real` on every
scenario, diagnoses meaningfully different across PVC / image-pull / OOM
/ missing-secret / RBAC.

### 7.7. Mac performance reference

| Metric | Approximate |
| ------ | ----------- |
| First call after `ollama serve` (cold model load) | 30-90 seconds |
| Warm calls per scenario (all 4 agents) | 10-25 seconds |
| All 5 scenarios end-to-end | 3-5 minutes |
| RAM with all 4 models loaded | ~12-14 GB |
| Disk for the 4 models | ~16 GB |

If your Mac has 8 GB RAM, run smaller variants or fewer agents at once
(see partial rollouts below).

### 7.8. Roll back to stub mode

Either unset the env vars in your current shell:

```bash
unset RCA_QWEN_URL RCA_DEEPSEEK_URL EXECUTOR_URL VALIDATOR_URL
```

…or close the uvicorn terminal and start a fresh one without sourcing
`.env`. Either way the next `analyze` call uses canned stubs again. The
pytest suite always runs in stub mode (env vars are explicitly unset by
the test runner).

### 7.9. Partial rollouts — only swap one agent

You don't need all four URLs set. Comment any subset out in `.env`:

```bash
# Only run agent_1 with a real model; others stay on stubs
RCA_QWEN_URL=http://localhost:11434
RCA_QWEN_MODEL=qwen2.5:7b
# RCA_DEEPSEEK_URL=http://localhost:11434   <- commented = stub
# EXECUTOR_URL=http://localhost:11434
# VALIDATOR_URL=http://localhost:11434
```

Useful for A/B testing a single model, debugging one agent at a time, or
hybrid demos where the validator stays deterministic for safety.

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
| All five payloads return identical generic diagnoses | You're in stub mode (no `*_URL` env vars set) | Follow Step 7 to wire Ollama; or check that `set -a && source .env && set +a` ran in the same shell as `uvicorn` |
| `agent_X real-empty-fallback` in the response notes | The model server returned empty content — usually a reasoning model that spent all its tokens on `<think>` blocks | Already mitigated by `<think>`-stripping in `model_client.py`; if persistent, bump `MODEL_TIMEOUT_S` or switch to a non-reasoning model |
| `agent_X real-error-fallback` in the response notes | The model server returned a transport error (404, 500, connection refused) | Verify the URL with `curl <url>/v1/models`; check `ollama list` shows the model name in `*_MODEL` |

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

Each agent runs in one of two modes:

- **Stub mode** (default): canned text, ~330ms per request, no models needed.
  Used by the pytest suite and any setup without `*_URL` env vars.
- **Real mode** (Step 7): calls a model server (Ollama on the Mac, vLLM /
  TGI / OpenAI-compat anywhere else). Diagnoses become evidence-aware
  and scenario-specific.

Both modes are wired identically in code — agents check their `*_URL`
env var on import and dispatch accordingly. Same pipeline, same schemas,
same tests.

---

## Appendix A — Future: real LoRA Docker images on a GPU box

The original spec called for these four custom Docker images:

- `mrunalikatta/executor-mistral-24b`
- `mrunalikatta/validator-llama-3b`
- `deveshs18/rca-lora-qwen`
- `deveshs18/rca-lora-deepseek`

These are amd64-only LoRA inference servers fine-tuned by the project
team. **They cannot run usefully on a Mac.** Three blockers:

1. **GPU required.** The images use `BitsAndBytesConfig` 4-bit
   quantization, which needs CUDA. Apple Silicon has no CUDA — even in
   a `linux/amd64` container under emulation, no NVIDIA hardware exists
   to attach to.
2. **Adapter files NOT included.** The container expects a directory at
   `/adapters` containing `adapter_config.json`, `adapter_model.safetensors`,
   and optionally `chat_template.jinja`. The Docker images bundle the
   inference framework only — adapter weights must be supplied by the
   publishers.
3. **Custom API contract.** The endpoint is `POST /v1/rca/generate` with
   structured K8s fields (`namespace`, `pod_name`, `event_reason`,
   `evidence_text`), not the OpenAI-compat `/v1/chat/completions` that
   `model_client.py` currently targets.

`docker-compose.yml` at the repo root is pre-configured for these images
on ports 11001-11004 with `platform: linux/amd64`. To use them when you
have GPU access:

A.1. **Get a Linux GPU box** — cloud (Lambda Labs A10 ~$0.50/hr, RunPod
     RTX 4090 ~$0.50/hr, AWS g5.xlarge ~$1/hr) or on-prem.

A.2. **Get the LoRA adapter directories** from `mrunalikatta` and
     `deveshs18`. Probably published to HuggingFace Hub or a private S3.

A.3. **Add `volumes:` mounts** to `docker-compose.yml` pointing each
     container's `/adapters` at the right local directory:

```yaml
validator:
  image: mrunalikatta/validator-llama-3b:latest
  environment:
    MODEL_PROFILE: llama
    ADAPTER_PATH: /adapters
  volumes:
    - ./adapters/validator-llama-3b:/adapters:ro
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            capabilities: [gpu]
```

A.4. **Update `fastapi/services/model_client.py:call_model()`** to POST
     to `/v1/rca/generate` with the structured K8s fields instead of
     `/v1/chat/completions` with a generic prompt. The function signature
     stays the same; the request body changes.

A.5. **Set the `*_URL` env vars** to the GPU box's IP/port:

```bash
export RCA_QWEN_URL=http://gpu-box-ip:11001
export RCA_DEEPSEEK_URL=http://gpu-box-ip:11002
export EXECUTOR_URL=http://gpu-box-ip:11003
export VALIDATOR_URL=http://gpu-box-ip:11004
```

For sanity-check work, **stick with Step 7 (Ollama)** — it's faster to
set up, runs natively on the Mac, and exercises the same architectural
surface area. Switch to LoRA images only when you need the team's
specific fine-tuning quality.

---

## Where to go next

- `README.md` — architecture, design decisions, status table
- `tests/test_sanity.py` — 13 automated tests, the source of truth for the
  contract
- `payloads/` — five real K8s incidents from
  `data/02-raw/k8s_combined_incidents.jsonl`, one per failure category
- Swagger UI at <http://localhost:8001/docs> while the server is running
