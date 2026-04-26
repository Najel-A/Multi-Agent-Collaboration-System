# Running the System End to End (Plain English)

This document explains, without assuming you know how to code, how to actually use the orchestrator — what to install, how to start it, and what each piece is for. There is one technical reference section at the bottom for engineers; you can ignore it.

For the *what does the orchestrator do?* explanation, see `how_the_orchestrator_works.md`.

---

## What this service is

This repository implements **one part of a larger system**: the part that does the actual reasoning about a Kubernetes incident.

The full architecture has four tiers:

```
       ┌────────────────────────┐
       │   1. Frontend          │      A web UI where a person
       │   (React app, browser) │      sees incidents and clicks "Analyze"
       └───────────┬────────────┘
                   │ "show me what to do"
                   ▼
       ┌────────────────────────┐
       │   2. Backend API       │      Stores incidents in a database,
       │   (your web service)   │      collects user feedback, persists results
       └───────────┬────────────┘
                   │ "here's an incident — analyze it"
                   ▼
       ┌────────────────────────┐
       │   3. Inference Service │  ◄── THIS REPO
       │   (this code)          │      Runs the multi-agent pipeline,
       │                        │      returns a structured answer
       └───────────┬────────────┘
                   │ calls the actual AI models
                   ▼
       ┌────────────────────────┐
       │   4. Model server      │      Ollama / vLLM / etc.
       │   (Ollama / vLLM)      │      Hosts the trained models
       └────────────────────────┘
```

This service (tier 3) is **stateless**: it doesn't store anything, doesn't know about users, and doesn't have its own database. It just receives an incident, runs the pipeline, and returns the answer. Tiers 1, 2, and 4 are out of scope for this repo.

---

## Quick start

```bash
# 1. One-time setup — install the two Python packages this service needs
python3 -m pip install fastapi uvicorn

# 2. Start the service (uses canned answers by default — no AI model needed)
uvicorn api.main:app --host 0.0.0.0 --port 8000

# 3. In another terminal, check it's alive
curl -s http://localhost:8000/health
```

You should see `{"status": "ok", "backend": "stub"}`. The service is now listening for incidents on port 8000.

`stub` means the service is using built-in canned answers — useful for testing the plumbing without running any actual AI model. To switch to real models, see "Hooking up real models" below.

---

## What you can do with it

The service has four web endpoints. You can call them from a browser, from `curl`, from your Backend API, or from any program that speaks HTTP.

| Address | What it does | Who uses it |
|---|---|---|
| `GET  /health` | "Are you alive?" — always replies if the service is running | Monitoring systems |
| `GET  /ready`  | "Are you actually ready to do work?" — checks the AI model server is reachable | Kubernetes readiness probe |
| `POST /analyze` | "Here is an incident; please analyze it" — returns the full structured answer | The Backend API tier |
| `POST /query` | Same thing in a different format used by an existing web app (LSA-WebApp) | LSA-WebApp's frontend |

That's the whole API.

---

## What an analysis looks like

You hand the service an incident, like this:

```json
{
  "scenario_id":   "imagepull_bad_tag",
  "namespace":     "infra-lab",
  "pod_name":      "worker-vkz64",
  "event_reason":  "Failed",
  "event_message": "Failed to pull image \"myreg.io/worker:v9.9.9\": manifest unknown",
  "pod_describe":  "Image: myreg.io/worker:v9.9.9\nReason: ImagePullBackOff"
}
```

It returns this structured answer (abbreviated):

```json
{
  "incident_id":   "worker-vkz64",
  "diagnosis":     "Pod is stuck because image tag v9.9.9 does not exist in the registry...",
  "fix_plan":      ["Update the deployment to a valid tag",
                    "Trigger a redeploy",
                    "Confirm the pod becomes Running"],
  "commands":      ["kubectl -n infra-lab set image deployment/worker worker=myreg.io/worker:v9.5.2",
                    "kubectl -n infra-lab rollout restart deployment/worker"],
  "verification":  ["The new image is pulled",
                    "Pod transitions Pending → Running"],
  "rollback":      ["kubectl -n infra-lab set image deployment/worker worker=<previous tag>"],
  "errors":        [],
  "approval_status": "pending",
  "trace":         [ ... full audit log of every step ... ]
}
```

The important things to notice:

- The answer is **structured** — every section is in its own field, ready for your UI to display.
- `approval_status` starts as **`"pending"`** — meaning, no human has yet approved running the commands. Your system MUST show these to a person and have them approve before anything executes.
- `errors` is normally empty. If something went wrong inside the pipeline (an AI model timed out, etc.), it shows up there — but you still get a 200 OK response with whatever partial output was produced.
- `trace` is a complete record of every internal message that happened during the analysis. Useful for demos, debugging, and audit logs.

---

## How does one analysis flow internally?

When you `POST /analyze` with an incident, here's what happens inside the service:

```
1. The orchestrator copies your incident (so your original isn't touched).
2. The Triage agent looks at it and decides "this is an ImagePullBackOff."
3. Eligible AI agents bid for the work — each says how confident they are.
4. The two highest bidders run in parallel and produce candidate diagnoses.
5. If the two diagnoses meaningfully disagree, that's recorded as a "conflict."
6. The Reconciler agent (Devstral) picks or merges them, writes the fix plan and commands.
7. The Validator agent (Qwen 35B or Llama 3B) writes verification + rollback steps.
8. Everything is bundled and returned, with approval_status="pending."
```

Behind the scenes, every step writes a message on a shared **whiteboard** (the Blackboard). The full whiteboard log is included in the response as `trace`, so a person reading the answer can see exactly who said what and when.

For a non-technical walkthrough of the agents and the whiteboard, see `how_the_orchestrator_works.md`.

---

## Hooking up real AI models

By default, the service uses canned answers (the `stub` backend) so you can run it with no AI infrastructure. Two real-backend options exist.

### Option 1 — Local Ollama (easiest)

[Ollama](https://ollama.com) is a free local AI model runner that works on a laptop or single GPU machine.

```bash
# 1. Install Ollama (one time): https://ollama.com
# 2. Pull a model
ollama pull qwen3.5:9b

# 3. Start ollama in the background
ollama serve &

# 4. Tell our service to use it
RCA_BACKEND=ollama OLLAMA_MODEL=qwen3.5:9b \
  uvicorn api.main:app --port 8000

# 5. Confirm it can reach the model
curl -s http://localhost:8000/ready
# → {"status": "ready", "backend_detail": "ollama 200"}
```

### Option 2 — Remote vLLM cluster (production)

[vLLM](https://github.com/vllm-project/vllm) is a high-performance model server typically run on GPUs in production.

```bash
RCA_BACKEND=vllm \
VLLM_URL=http://your-vllm-server:8000/v1 \
VLLM_MODEL=qwen3.5:9b \
VLLM_API_KEY=your-key \
  uvicorn api.main:app --port 8000
```

`/ready` will report 503 (Service Unavailable) if it can't reach the model server, which is what you want — Kubernetes will know not to send traffic.

---

## Locking down the service

By default, anyone who can reach the service's port can call it. For real deployments, set an API key:

```bash
RCA_API_KEY=some-long-random-string \
  uvicorn api.main:app --port 8000
```

After that, callers must send an `X-API-Key: some-long-random-string` header on `/analyze` and `/query`. `/health` and `/ready` stay open so monitoring systems can probe without needing the key.

---

## Configuration cheat sheet

All configuration is via environment variables, set when you start the service:

| Variable | Default | What it controls |
|---|---|---|
| `RCA_BACKEND` | `stub` | Which model backend: `stub` (canned answers), `ollama` (local), or `vllm` (remote) |
| `RCA_API_KEY` | unset | If set, all analysis requests must send `X-API-Key` matching this value |
| `OLLAMA_URL` | `http://localhost:11434` | Where local Ollama is listening |
| `OLLAMA_MODEL` | `qwen3.5:9b` | Which model Ollama should serve |
| `VLLM_URL` | (none) | Where the vLLM server is listening |
| `VLLM_MODEL` | `qwen3.5:9b` | Which model vLLM is serving |
| `VLLM_API_KEY` | unset | Bearer token for the vLLM server |

---

## Common questions

**Q: Can I run this on my laptop?**
Yes, with the `stub` backend (no AI needed) or with Ollama running locally (needs ~16 GB RAM for a small model). The service is the same code in both cases.

**Q: Can it execute kubectl commands automatically?**
No. The service produces a list of commands as text, marked PENDING APPROVAL. Actually running them requires a separate, deliberate approval step that a human must take. There is no path to auto-execution.

**Q: What happens if a model takes too long?**
Each agent has a 60-second time limit and the whole pipeline has a 5-minute time limit (both configurable). If a limit is hit, the slow step is marked as a timeout in the `errors` field, and the rest of the pipeline continues with whatever was produced.

**Q: How do I know the answer is correct?**
You don't, automatically. The system reduces the chance of wrong answers (two independent agents + an arbiter + a validator) and makes wrong answers easier to *spot* (full audit trail in `trace`). Final judgment is up to a human reviewing the result.

**Q: Where does the result get stored?**
Nowhere by this service — it's stateless. Storing results is the Backend API tier's job (typically a database like MongoDB or Postgres).

---

## Troubleshooting

| Symptom | What's likely wrong | Fix |
|---|---|---|
| `ModuleNotFoundError: fastapi` | The Python packages aren't installed | `python3 -m pip install fastapi uvicorn` |
| `/ready` returns 503 with "ollama unreachable" | Ollama isn't running, or wrong URL | Start `ollama serve`; check `OLLAMA_URL` |
| `/health` says `"backend": "stub"` even though I set `RCA_BACKEND=ollama` | Env vars are read at startup; you set it after starting | Restart the service after changing env vars |
| `/analyze` returns 401 "invalid or missing X-API-Key" | `RCA_API_KEY` is set but caller didn't send the header | Send `X-API-Key: <value>`, or unset the env var for dev |
| Got 200 OK but `errors` field is non-empty | One step degraded — partial output was produced | Read `errors` to see which step; check the model server's health |
| `CommandsNotApprovedError` when trying to execute | Result wasn't approved before execute_commands() was called | Call `result.approve(approver, note)` first, or use the `approval_callback` parameter |
| Slow first response after starting | Large model is warming up | Normal; subsequent requests will be faster |

---

## Engineering appendix

Reference for developers. Skip if you're not writing code against this service.

**Endpoints.**
- `GET  /health` — `{status, backend}` — always 200 if process is up.
- `GET  /ready`  — Probes the configured backend; 200 with `backend_detail` or 503.
- `POST /analyze` — Body: flat incident schema (see `data/02-raw/k8s_combined_incidents.jsonl`). Returns `StructuredRCAResult.to_dict()`.
- `POST /query` — Body: `NexusAnalyzeRequestBody` (LSA-WebApp's contract). Returns `{text}` formatted to five section headers.

**Auth.** When `RCA_API_KEY` is set, `/analyze` and `/query` require `X-API-Key`. `/health` and `/ready` are always open.

**StructuredRCAResult schema.**
```
incident_id, diagnosis, fix_plan[], commands[], verification[], rollback[],
agent_1_solution{}, agent_2_solution{}, reconciliation_notes,
agent_results{agent_1, agent_2, reconciler, validator},
duration_ms, errors[],
approval_status (pending|approved|rejected), approver, approval_note,
trace[]  (full audit log of Blackboard messages)
```

**Approval gate.** `from agents.orchestrator import execute_commands; execute_commands(result, runner)` is the only sanctioned executor. Raises `CommandsNotApprovedError` unless `result.approval_status == "approved"`. The runner callable owns environment-specific safety (kubectl context, namespace allow-list, dry-run, etc.).

**CLI entry point.** `python -m agents.run_rca --count 5 [--backend ollama|vllm] [--mode bootstrap|roles]` — runs the same pipeline against the JSONL dataset without HTTP.

**Tests.** `python3 tests/test_orchestrator.py` runs all 18 unit + integration tests (stub end-to-end + live Ollama gating).

**Files.**
- `api/main.py` — FastAPI app: `/health`, `/ready`, `/analyze`, `/query`.
- `agents/orchestrator.py` — `Orchestrator`, `StructuredRCAResult`, `format_as_sections`, `execute_commands`.
- `agents/blackboard.py` — `Blackboard`, `Message`, `Topics`.
- `agents/registry.py` — `AgentRegistry`, `Capability`.
- `agents/triage_agent.py` — Model-free routing agent.
- `agents/{base,solution_generator,reconciliation,validation}_agent.py` — Core agents.
- `agents/model_loaders.py` — Ollama / vLLM HTTP adapters.

**Related reading.** `how_the_orchestrator_works.md` — non-technical explanation of the agents and the Blackboard pattern.
