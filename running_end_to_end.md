# Running the Inference Service End-to-End

How to run the multi-agent RCA pipeline as a stateless inference service — `POST` an incident in, get a `StructuredRCAResult` back. This repo implements only the **FastAPI Inference Service** tier of the architecture diagram:

```
User
  │
  ▼
Frontend (React UI)                          ← out of scope (e.g. LSA-WebApp)
  │
  ▼
Backend API (Incident Retrieval /            ← out of scope
Orchestration / Feedback)
  │
  ▼
FastAPI Inference Service                    ← THIS REPO (api/main.py)
  │
  ▼
Agent 1 / Agent 2                            ← agents/solution_generator_agent.py
Decision / Reconciliation                    ← agents/reconciliation_agent.py
Validation                                   ← agents/validation_agent.py
  │
  ▼
Structured RCA Result                        ← returned as JSON to caller
```

The service is **stateless** — it doesn't read or write any data store. It receives an incident, runs the orchestrator, returns the result. Persistence belongs to the Backend API tier.

---

## Quick start

```bash
# 1. Install runtime deps (one time)
python3 -m pip install fastapi uvicorn

# 2. Start the inference service (defaults to stub backend — no model required)
uvicorn api.main:app --host 0.0.0.0 --port 8000

# 3. Smoke-test it
curl -s http://localhost:8000/health | jq
curl -s http://localhost:8000/ready  | jq
```

Default backend is `stub` — canned SFT-shaped responses. Works with zero model setup. Switch to real models via env vars (see below).

---

## Endpoints

| Method | Path | Purpose | Auth |
|---|---|---|---|
| `GET`  | `/health`  | Process liveness — always 200 if running | open |
| `GET`  | `/ready`   | Probes the model backend; 503 if unreachable | open |
| `POST` | `/analyze` | Structured incident → `StructuredRCAResult` JSON | gated by `RCA_API_KEY` |
| `POST` | `/query`   | LSA-WebApp prompt contract → text with the five RCA section headers | gated by `RCA_API_KEY` |

If `RCA_API_KEY` is unset, all endpoints are open (dev mode). If set, `/analyze` and `/query` require an `X-API-Key: <key>` header. `/health` and `/ready` stay open so monitoring can probe without credentials.

---

## End-to-end trace of one `/analyze` call

```
1. Caller POST /analyze with the incident JSON body.
2. FastAPI validates via Pydantic AnalyzeRequest, requires pod_describe OR event_message.
3. orchestrator.analyze(incident) runs:
     ├─ work = copy.deepcopy(incident)        # caller's dict is never touched
     ├─ Agent 1 (solution_generator_agent)    → candidate diagnosis 1   ─┐
     ├─ Agent 2 (solution_generator_agent)    → candidate diagnosis 2   ─┤  parallel
     │      (each capped at min(60s, deadline_remaining); errors → result.errors)
     ├─ Reconciler (reconciliation_agent)     → diagnosis + fix_plan + commands
     └─ Validator (validation_agent)          → verification + rollback
4. StructuredRCAResult.to_dict() → JSON response.
5. Result returns with approval_status="pending". Caller MUST approve before
   executing any command via execute_commands(result, runner).
```

The `/query` path runs the same pipeline but receives the LSA-WebApp's free-form prompt body, extracts the evidence between `--- INCIDENT EVIDENCE ---` markers, and renders the result through `format_as_sections()` so the response body matches the headers `parseRcaResponse.ts` expects.

---

## Switching from stub to real models

### Local Ollama

```bash
# 1. Pull the model and start the daemon
ollama serve &
ollama pull qwen3.5:9b

# 2. Point the API at it
RCA_BACKEND=ollama OLLAMA_MODEL=qwen3.5:9b \
  uvicorn api.main:app --port 8000
```

`/ready` will now probe `OLLAMA_URL/api/tags` (default `http://localhost:11434`) and return 503 if the daemon isn't reachable.

### Remote vLLM or any OpenAI-compatible endpoint

```bash
RCA_BACKEND=vllm \
VLLM_URL=http://vllm.internal:8000/v1 \
VLLM_MODEL=qwen3.5:9b \
VLLM_API_KEY=... \
  uvicorn api.main:app --port 8000
```

`/ready` probes `VLLM_URL/models`.

---

## HTTP API reference

### `GET /health`

```json
{
  "status": "ok",
  "backend": "stub"
}
```

### `GET /ready`

```json
{ "status": "ready", "backend_detail": "ollama 200" }
```

`503` if the configured backend cannot be reached within ~2s.

### `POST /analyze`

**Headers**: `X-API-Key: <RCA_API_KEY>` (only when `RCA_API_KEY` is set).

**Request body** — flat incident schema (same shape as `data/02-raw/k8s_combined_incidents.jsonl`):

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

All fields are optional; at least `pod_describe` or `event_message` must be present (else 400).

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
  "duration_ms":          7432.1,
  "errors":               [],
  "approval_status":      "pending",
  "approver":             null,
  "approval_note":        ""
}
```

Notes on the safety fields:

- **`errors`** — non-empty if any agent failed or timed out. The pipeline degrades to partial output rather than raising, so always check this list when handling the result.
- **`approval_status`** — always `"pending"` from a fresh `/analyze` call. The Backend API tier is responsible for collecting human approval before executing any command. `execute_commands(result, runner)` (in `agents.orchestrator`) raises `CommandsNotApprovedError` unless the result has been explicitly approved.

### `POST /query`

LSA-WebApp's `NexusAnalyzeRequestBody` contract.

**Request body**:

```json
{
  "system_prompt": "...",
  "prompt": "Analyze ...\n--- INCIDENT EVIDENCE ---\n<text>\n--- END EVIDENCE ---",
  "max_new_tokens": 1024,
  "max_time": 120,
  "temperature": 0,
  "top_p": 1
}
```

`max_time` becomes the per-request `total_timeout_s` for the orchestrator. `system_prompt`, `temperature`, `top_p`, `max_new_tokens` are accepted but currently unused — the orchestrator's prompts are role-fixed.

**Response body**:

```json
{ "text": "Diagnosis\n...\n\nStep-by-Step Fix Plan\n1. ...\n\nConcrete Actions or Commands to Apply the Fix\n- [PENDING APPROVAL] kubectl ...\n\nVerification Steps to Confirm the Fix Worked\n- ...\n\nRollback Guidance if the Fix Causes Issues\n- ..." }
```

Headers match `parseRcaResponse.ts` exactly. Commands stay prefixed with `[PENDING APPROVAL]` until the underlying `StructuredRCAResult` is approved via `result.approve(approver)`.

### Example curl

```bash
curl -sX POST http://localhost:8000/analyze \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $RCA_API_KEY" \
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

(Drop the `X-API-Key` header if `RCA_API_KEY` is unset.)

---

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `RCA_BACKEND` | `stub` | `stub` / `ollama` / `vllm` — picks the model backend |
| `RCA_API_KEY` | — | If set, `/analyze` and `/query` require `X-API-Key: <key>` |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama daemon address |
| `OLLAMA_MODEL` | `qwen3.5:9b` | Model tag used for all four agent slots in bootstrap mode |
| `VLLM_URL` | — (required if `RCA_BACKEND=vllm`) | OpenAI-compatible base URL |
| `VLLM_MODEL` | `qwen3.5:9b` | Model name the vLLM server serves |
| `VLLM_API_KEY` | — | Bearer token for hosted endpoints |

---

## Files

- `api/__init__.py` — package marker
- `api/main.py` — FastAPI app: `/health`, `/ready`, `/analyze`, `/query`
- `agents/orchestrator.py` — `Orchestrator`, `StructuredRCAResult`, `format_as_sections`, `execute_commands`
- `agents/{base,solution_generator,reconciliation,validation}_agent.py` — the four agents
- `agents/model_loaders.py` — Ollama / vLLM client adapters
- `agents/run_rca.py` — CLI entry point (alternative to the HTTP service)

Runs on stdlib + FastAPI + uvicorn; no React / Node toolchain needed.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'fastapi'`**
Run `python3 -m pip install fastapi uvicorn`.

**`/ready` returns 503 with `ollama unreachable`**
`ollama serve` isn't running, or `OLLAMA_URL` points at the wrong port. Test directly: `curl http://localhost:11434/api/tags` should list pulled models.

**`/health` says `"backend": "stub"` even though I set `RCA_BACKEND=ollama`**
Env vars are read at process startup. Restart uvicorn after changing them. With `--reload`, editing `api/main.py` triggers a restart automatically; env-var changes do not.

**`/analyze` returns `401 invalid or missing X-API-Key`**
You set `RCA_API_KEY`. Either send the matching `X-API-Key` header on the request, or unset the env var for local dev.

**`result.errors` is non-empty but I got a 200**
By design. Agent failures and timeouts are captured per-step rather than raising; the response gives you partial output plus the error list. Inspect `result.errors` to see which step degraded.

**`CommandsNotApprovedError` when calling `execute_commands(result, runner)`**
`result.approval_status` is `"pending"` (or `"rejected"`). Call `result.approve(approver, note="…")` first, or pass `approval_callback=` to `Orchestrator.analyze()` to gate inline.

**Ollama backend hangs / times out**
The orchestrator caps each agent at 60 s and the whole pipeline at 300 s by default. Large models (35 B) may need a warmup call. If you keep hitting timeouts, raise `agent_timeout_s` / `total_timeout_s` when constructing `Orchestrator`, or supply a per-request `total_timeout_s` via `/query`'s `max_time`.

**`ValueError: 'foo' is not an approved 'rca' model`**
`from_role_defaults` validates against `AGENT_ROLE_MODELS` in `agents/orchestrator.py`. For dev, set `RCA_BACKEND=stub` or pass an approved model name via `OLLAMA_MODEL` / `VLLM_MODEL`.
