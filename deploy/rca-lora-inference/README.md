# RCA LoRA inference (Docker)

HTTP API for your **trained LoRA adapters** under `Training_Devesh/`.

- **Weights:** LoRA files are **mounted read-only** from your disk (`adapter_model.safetensors`, `adapter_config.json`, etc.).
- **Tokenizer:** Loaded from **Hugging Face** using `base_model_name_or_path` in `adapter_config.json` (same as training, so decoding has correct spaces). If `chat_template.jinja` exists next to the adapter, that template is applied.
- **Base model:** Downloaded from **Hugging Face** on first start (large; cached in the named Docker volume `*-hf-cache`).

## What to hand off

1. This folder: `deploy/rca-lora-inference/` (`Dockerfile`, `app.py`, `docker-compose.yml`, `requirements.txt`, this `README.md`).
2. Adapter directories (or a zip), for example:
   - `Training_Devesh/qwen_rca_8b-20260415T223457Z-3-001/qwen_rca_8b/`
   - `Training_Devesh/deepseek final-20260419T233959Z-3-001/deepseek final/`  
   Paths with spaces are fine if the compose volume path stays quoted (see `docker-compose.yml`).

**Adapter folder should include:** `adapter_model.safetensors`, `adapter_config.json`, `tokenizer.json`, `tokenizer_config.json`, and **`chat_template.jinja`** (recommended so prompts match training).

## What downloads automatically (no manual HF “clone”)

| When | What | Where it goes |
|------|------|----------------|
| `docker compose build` | PyTorch CUDA **image layers** | Docker image cache |
| First **`up`** for a profile | **Base model** weights for the id in `adapter_config.json` (e.g. `Qwen/Qwen2.5-7B-Instruct`, `deepseek-ai/deepseek-llm-7b-chat`) | Volume `qwen-hf-cache` or `deepseek-hf-cache` → `HF_HOME=/hf-cache` in the container |
| Same first start | **Tokenizer** files for that base id (small) | Same HF cache volume |

You do **not** need to download the base model separately on the host; the container pulls it if the volume is empty. **Internet** is required on that first start.

Optional: create `.env` in this folder with `HF_TOKEN=...` if a repo is gated or you want higher Hub rate limits.

## Prerequisites

- NVIDIA GPU + driver, **Docker Desktop** (or Linux Docker) with **GPU** enabled for containers.
- Enough disk for HF cache (many GB per base model).

## Build and run (one model at a time on one GPU)

From `deploy/rca-lora-inference/`:

```bash
docker compose build
# Foreground (logs in this terminal):
docker compose --profile qwen up
# Or background:
docker compose --profile qwen up -d
```

| Profile | URL after `up` |
|---------|----------------|
| `qwen` | `http://localhost:8001` |
| `deepseek` | `http://localhost:8002` |

**DeepSeek:** stop Qwen first, then:

```bash
docker compose --profile qwen down
docker compose --profile deepseek build
docker compose --profile deepseek up -d
```

**Follow logs:**

```bash
docker compose --profile qwen logs -f
```

Wait until you see **`Application startup complete.`** before calling the API (first start can take many minutes while shards download).

**Stop (keep HF cache for next time):**

```bash
docker compose --profile qwen down
```

**Stop and delete HF cache volumes:**

```bash
docker compose --profile qwen down -v
```

## API

- `GET /health` — returns `status`, `model_profile`, `base_model_id` when ready.
- `POST /v1/rca/generate` — JSON body:

```json
{
  "evidence_text": "...",
  "namespace": "default",
  "pod_name": "my-pod",
  "pod_status": "CrashLoopBackOff",
  "event_reason": "BackOff",
  "event_message": "...",
  "max_new_tokens": 512,
  "temperature": 0.0,
  "do_sample": false
}
```

### Examples

**Linux / macOS / Git Bash:**

```bash
curl -s http://127.0.0.1:8001/health
curl -s http://127.0.0.1:8001/v1/rca/generate \
  -H "Content-Type: application/json" \
  --data-raw '{"evidence_text":"Pod keeps restarting...","namespace":"default","pod_name":"demo"}'
```

### Windows PowerShell

`curl` is **`Invoke-WebRequest`** — use **`Invoke-RestMethod`** or **`curl.exe`**.

**Health (wait until startup finished):**

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
```

**Generate (recommended):**

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8001/v1/rca/generate" `
  -ContentType "application/json; charset=utf-8" `
  -Body (@{
    evidence_text = "Pod keeps crashing in CrashLoopBackOff"
    namespace     = "default"
    pod_name      = "demo"
    max_new_tokens = 256
  } | ConvertTo-Json -Compress)
```

Use port **8002** for DeepSeek. If the connection drops, the container is probably still loading—check `docker compose --profile <name> logs -f`.

**`curl.exe` + JSON file (UTF-8, no BOM):**

```powershell
$json = @'
{"evidence_text":"Pod keeps crashing in CrashLoopBackOff","namespace":"default","pod_name":"demo"}
'@
[System.IO.File]::WriteAllText("$PWD\rca-payload.json", $json.Trim(), [System.Text.UTF8Encoding]::new($false))
curl.exe -s http://127.0.0.1:8001/v1/rca/generate -H "Content-Type: application/json" --data-binary "@rca-payload.json"
```

## Custom adapter path

Edit the `volumes:` paths in `docker-compose.yml`, or run a single container:

```bash
docker build -t rca-lora .
docker run --rm --gpus all -p 8001:8000 \
  -e MODEL_PROFILE=qwen \
  -e ADAPTER_PATH=/adapters \
  -e HF_HOME=/hf-cache \
  -v /path/to/your_adapter_dir:/adapters:ro \
  -v hf-cache:/hf-cache \
  rca-lora
```

## Troubleshooting

- **`AttributeError: ... 'set_submodule'`** — rebuild with the current `Dockerfile` (PyTorch **2.5.1**): `docker compose build --no-cache`.
- **`500` / chat template errors** — keep **`chat_template.jinja`** next to the adapter; `app.py` applies it after loading the hub tokenizer.
- **Connection closed / failed requests right after `up`** — wait for **`Application startup complete`** in logs; first download is slow.
- **Exit code `137`** — OOM. Close other GPU apps, raise Docker memory, lower `max_new_tokens`, run **one** profile at a time.
- **DeepSeek text had no spaces (`Thepodenters...`)** — fixed by loading the **hub** tokenizer (see intro). Rebuild image if you still see old behavior.

## Notes

- **VRAM:** do not run `qwen` and `deepseek` profiles on one small GPU at the same time.
- **Prompts:** `MODEL_PROFILE=qwen` uses system + user chat (Qwen notebook style); `deepseek` uses the single-user RCA format from the DeepSeek notebook.
