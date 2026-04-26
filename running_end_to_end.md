# Running the Orchestrator End-to-End

How to run the multi-agent RCA pipeline all the way from an incident pasted into the dashboard to a structured RCA result rendered back in the UI.

Matches the architecture diagram:

```
User → Frontend (dashboard)
        ↓
   Backend API                     ← api/main.py routes
   (Incident Retrieval /
    Orchestration / Feedback)
        ↓
   FastAPI Inference Service       ← api/main.py::_build_orchestrator
        ↓
   Agent 1 / Agent 2               ← agents/solution_generator_agent.py
   Decision / Reconciliation       ← agents/reconciliation_agent.py
   Validation                      ← agents/validation_agent.py
        ↓
   Structured RCA Result           ← returned as JSON → rendered in dashboard
```

---

## Quick start

```bash
# 1. Install the web deps (one time)
python3 -m pip install fastapi uvicorn

# 2. Start the inference service
uvicorn api.main:app --reload --port 8000

# 3. Open the dashboard
open http://localhost:8000/
```

That's it. The dashboard lets you either paste an incident JSON or pick one from the **Load sample** dropdown (pre-populated with distinct scenarios from `k8s_combined_incidents.jsonl`), click **Analyze**, and see the full structured result rendered — diagnosis, fix plan, commands, verification, and rollback.

Default backend is `stub` — canned SFT-shaped responses. Works with zero model setup. Switch to real models via env vars (see below).

---

## What each piece does

| Layer | File | Role in the diagram |
|---|---|---|
| User → Frontend | `api/dashboard.html` | React-UI-equivalent: paste incident, click Analyze, render diagnosis / fix / commands / verification / rollback |
| Backend API | `api/main.py` routes | `GET /` serves dashboard · `GET /incidents/samples` · `POST /analyze` · `GET /health` |
| FastAPI Inference Service | `api/main.py::_build_orchestrator` | Instantiates one `Orchestrator` at startup; dispatches each `/analyze` request to it |
| Agent 1 / Agent 2 / Reconciler / Validator | `agents/*.py` | Unchanged from the CLI path — same agents |
| Data Layer (incidents) | `data/02-raw/k8s_combined_incidents.jsonl` | Read by `/incidents/samples` for the sample dropdown |

---

## End-to-end trace of one click

```
1. User picks "pvc_not_found_mountfail" in the dashboard, clicks Analyze.
2. dashboard.html POST /analyze with the incident JSON body.
3. FastAPI validates via Pydantic (AnalyzeRequest), checks pod_describe exists.
4. orchestrator.analyze(incident) is called:
     ├─ Agent 1 (solution_generator_agent.py)   → candidate diagnosis 1
     └─ Agent 2 (solution_generator_agent.py)   → candidate diagnosis 2   [parallel]
            └─ Reconciler (reconciliation_agent.py)  → diagnosis + fix + commands
                 └─ Validator (validation_agent.py)  → verification + rollback
5. StructuredRCAResult.to_dict() → JSON response.
6. dashboard.html renders diagnosis, fix plan, commands (as <code>), verification,
   rollback, reconciliation notes, and a collapsible raw-JSON view.
```

---

## Switching from stub to real models

The default backend is `stub` so the dashboard works immediately with no model setup. Swap in real models via env vars:

### Local Ollama

```bash
# 1. Pull the model and start the daemon
ollama serve &
ollama pull qwen3.5:9b

# 2. Point the API at it
RCA_BACKEND=ollama OLLAMA_MODEL=qwen3.5:9b \
  uvicorn api.main:app --port 8000
```

Optional: `OLLAMA_URL=http://localhost:11434` (default).

### Remote vLLM or any OpenAI-compatible endpoint

```bash
RCA_BACKEND=vllm \
VLLM_URL=http://vllm.internal:8000/v1 \
VLLM_MODEL=qwen3.5:9b \
VLLM_API_KEY=... \
  uvicorn api.main:app --port 8000
```

The `/health` endpoint reports which backend is active, and the dashboard header shows it too — so you can tell at a glance whether you're looking at stub output or real inference.

---

## HTTP API reference

### `GET /`
Serves the dashboard HTML.

### `GET /health`
```json
{
  "status": "ok",
  "backend": "stub",
  "data_path_exists": true
}
```

### `GET /incidents/samples?count=5`
Returns up to `count` distinct-scenario incidents from `k8s_combined_incidents.jsonl`. Used by the dashboard's sample dropdown.

### `POST /analyze`

**Request body** — flat incident schema (same shape as `k8s_combined_incidents.jsonl`):

```json
{
  "scenario_id":   "createcontainerconfigerror_missing_secret",
  "namespace":     "team-b-stg",
  "pod_name":      "service-w-bm4b-0",
  "pod_status":    "Pending",
  "event_reason":  "Failed",
  "event_message": "Error: secret \"db-credentials\" not found",
  "pod_describe":  "Name: service-w-bm4b-0 ..."
}
```

All fields are optional except that at least `pod_describe` or `event_message` must be present (else returns 400).

**Response body** — `StructuredRCAResult.to_dict()`:

```json
{
  "incident_id":          "service-w-bm4b-0",
  "diagnosis":            "Pod is stuck in CreateContainerConfigError because ...",
  "fix_plan":             ["Create the missing Secret ...", "..."],
  "commands":             ["kubectl -n team-b-stg create secret ...", "..."],
  "verification":         ["Secret exists ...", "Pod transitions to Running", "..."],
  "rollback":             ["kubectl -n team-b-stg delete secret ...", "..."],
  "agent_1":              {"diagnosis": "...qwen3.5:9b output..."},
  "agent_2":              {"diagnosis": "...deepseek-r1:8b output..."},
  "reconciliation_notes": "Both agents identified the missing Secret. No arbitration needed.",
  "duration_ms":          7432.1
}
```

### Example curl

```bash
curl -sX POST http://localhost:8000/analyze \
  -H 'Content-Type: application/json' \
  -d @- <<'EOF' | jq
{
  "scenario_id": "imagepull_bad_tag",
  "namespace": "infra-lab",
  "pod_name": "worker-vkz64",
  "pod_status": "Pending",
  "event_message": "Failed to pull image \"myreg.io/worker:v9.9.9\": manifest unknown",
  "pod_describe": "Image: myreg.io/worker:v9.9.9\nReason: ImagePullBackOff"
}
EOF
```

---

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `RCA_BACKEND` | `stub` | `stub` / `ollama` / `vllm` — picks the model backend |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama daemon address |
| `OLLAMA_MODEL` | `qwen3.5:9b` | Model tag used for all four agent slots in bootstrap mode |
| `VLLM_URL` | — (required if `RCA_BACKEND=vllm`) | OpenAI-compatible base URL |
| `VLLM_MODEL` | `qwen3.5:9b` | Model name the vLLM server serves |
| `VLLM_API_KEY` | — | Bearer token for hosted endpoints |

---

## Files

- **`api/__init__.py`** — package marker
- **`api/main.py`** — FastAPI app with 4 routes
- **`api/dashboard.html`** — minimal vanilla-JS dashboard (no build step)

Total new code: ~260 lines. Runs on stdlib + FastAPI + uvicorn; no React / Node toolchain needed. When you're ready for a real React frontend, point it at the same endpoints — the API contract doesn't change.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'fastapi'`**
Run `python3 -m pip install fastapi uvicorn` (use `python3 -m pip`, not bare `pip` — they can point to different environments on macOS).

**Dashboard says "backend = stub" even though I set `RCA_BACKEND=ollama`**
Env vars are read at process startup. Restart uvicorn after changing them. With `--reload`, editing `api/main.py` triggers a restart automatically; env var changes do not.

**`/health` shows `"data_path_exists": false`**
`data/02-raw/k8s_combined_incidents.jsonl` is missing. The `/analyze` endpoint still works — you just can't use the sample dropdown. Paste incident JSON manually.

**Ollama backend hangs / times out**
Check `curl http://localhost:11434/api/tags` — should list pulled models. If empty, run `ollama pull <model>` first. Large models (35 B) may need up to 60 s for the first call to warm up.

**`ValueError: 'foo' is not an approved 'rca' model`**
`from_role_defaults` validates against `AGENT_ROLE_MODELS` in `agents/orchestrator.py`. For bootstrap / dev use, set `RCA_BACKEND=stub` or pass an approved model name via `OLLAMA_MODEL` / `VLLM_MODEL`.
