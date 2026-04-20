"""
HTTP inference for RCA LoRA adapters (Qwen chat vs DeepSeek user-prompt style).
Base model id is read from adapter_config.json; weights load from Hugging Face on first start unless cached.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

MODEL_PROFILE = os.environ.get("MODEL_PROFILE", "qwen").strip().lower()
ADAPTER_PATH = Path(os.environ["ADAPTER_PATH"]).resolve()
MAX_SEQ_LEN = int(os.environ.get("MAX_SEQ_LEN", "2048"))
LOAD_IN_4BIT = os.environ.get("LOAD_IN_4BIT", "true").lower() in ("1", "true", "yes")

SYSTEM_PROMPT_QWEN = (
    "You are a Kubernetes SRE performing root cause analysis. "
    "Given pod and event evidence, produce a concise RCA with: "
    "(1) what failed, (2) the likely root cause in Kubernetes terms, "
    "and (3) 2 to 4 short remediation bullets."
)

_state: dict[str, Any] = {}


def _read_base_model_id() -> str:
    cfg = ADAPTER_PATH / "adapter_config.json"
    if not cfg.is_file():
        raise FileNotFoundError(f"Missing adapter_config.json under {ADAPTER_PATH}")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    bid = data.get("base_model_name_or_path")
    if not bid:
        raise KeyError("adapter_config.json missing base_model_name_or_path")
    return str(bid)


def _compact_evidence(row: dict[str, Any]) -> str:
    evidence = (row.get("evidence_text") or "").strip()
    namespace = row.get("namespace", "") or ""
    pod_name = row.get("pod_name", "") or ""
    pod_status = row.get("pod_status", "") or ""
    event_reason = row.get("event_reason", "") or ""
    event_message = row.get("event_message", "") or ""

    header = (
        f"Namespace: {namespace}\n"
        f"Pod: {pod_name}\n"
        f"Pod status: {pod_status}\n"
        f"Event reason: {event_reason}\n"
        f"Event message: {event_message}\n"
    )
    return header + "\nEvidence:\n" + evidence


def _compact_evidence_deepseek(row: dict[str, Any]) -> str:
    evidence = (row.get("evidence_text") or "").strip()
    namespace = row.get("namespace", "") or ""
    pod_name = row.get("pod_name", "") or ""
    pod_status = row.get("pod_status", "") or ""
    event_reason = row.get("event_reason", "") or ""
    event_message = row.get("event_message", "") or ""

    parts = [
        "Task: Analyze the Kubernetes incident and produce RCA.",
        "",
        "Return exactly this format:",
        "Root cause: ...",
        "Why: ...",
        "Remediation:",
        "- ...",
        "- ...",
        "",
        "Incident evidence:",
        f"Namespace: {namespace}",
        f"Pod: {pod_name}",
        f"Pod status: {pod_status}",
        f"Event reason: {event_reason}",
        f"Event message: {event_message}",
        "",
        "Evidence:",
        evidence,
    ]
    return "\n".join(parts).strip()


def _build_messages_qwen(row: dict[str, Any]) -> list[dict[str, str]]:
    user_text = (
        "Analyze the following Kubernetes incident evidence and provide the RCA.\n\n"
        + _compact_evidence(row)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT_QWEN},
        {"role": "user", "content": user_text},
    ]


def _build_messages_deepseek(row: dict[str, Any]) -> list[dict[str, str]]:
    return [{"role": "user", "content": _compact_evidence_deepseek(row)}]


def _decode_new_tokens(tok, new_ids: list[int]) -> str:
    return tok.decode(new_ids, skip_special_tokens=True).strip()


def _ensure_chat_template(tokenizer) -> None:
    """Prefer chat_template.jinja from the adapter (training parity); else require hub tokenizer template."""
    jinja = ADAPTER_PATH / "chat_template.jinja"
    if jinja.is_file():
        tokenizer.chat_template = jinja.read_text(encoding="utf-8")
        return
    if getattr(tokenizer, "chat_template", None):
        return
    raise ValueError(
        f"No chat template on tokenizer and missing {jinja.name} under {ADAPTER_PATH}. "
        "Copy chat_template.jinja next to the adapter or set tokenizer.chat_template."
    )


def _load_model() -> None:
    base_id = _read_base_model_id()
    # Tokenizer JSON saved next to LoRA weights can use LlamaTokenizerFast "legacy" decode that drops spaces.
    # Load tokenizer from the same base repo as training so ids decode correctly; still mount LoRA from ADAPTER_PATH.
    tokenizer = AutoTokenizer.from_pretrained(base_id, trust_remote_code=True)
    _ensure_chat_template(tokenizer)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=LOAD_IN_4BIT,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=compute_dtype,
    )

    base = AutoModelForCausalLM.from_pretrained(
        base_id,
        quantization_config=bnb_config if LOAD_IN_4BIT else None,
        device_map="auto",
        trust_remote_code=True,
    )
    base.config.use_cache = False
    model = PeftModel.from_pretrained(base, str(ADAPTER_PATH))
    model.eval()

    _state["tokenizer"] = tokenizer
    _state["model"] = model
    _state["base_model_id"] = base_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not ADAPTER_PATH.is_dir():
        raise RuntimeError(f"ADAPTER_PATH is not a directory: {ADAPTER_PATH}")
    if MODEL_PROFILE not in ("qwen", "deepseek"):
        raise RuntimeError("MODEL_PROFILE must be 'qwen' or 'deepseek'")
    _load_model()
    yield
    _state.clear()


app = FastAPI(title="RCA LoRA inference", lifespan=lifespan)


class IncidentIn(BaseModel):
    evidence_text: str = ""
    namespace: str = ""
    pod_name: str = ""
    pod_status: str = ""
    event_reason: str = ""
    event_message: str = ""
    max_new_tokens: int = Field(512, ge=1, le=2048)
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    do_sample: bool = False


class GenerateOut(BaseModel):
    text: str
    model_profile: Literal["qwen", "deepseek"]
    base_model_id: str


@app.get("/health")
def health() -> dict[str, str]:
    ok = "tokenizer" in _state and "model" in _state
    return {
        "status": "ok" if ok else "loading",
        "model_profile": MODEL_PROFILE,
        "adapter_path": str(ADAPTER_PATH),
        "base_model_id": _state.get("base_model_id", ""),
    }


@app.post("/v1/rca/generate", response_model=GenerateOut)
def generate_rca(body: IncidentIn) -> GenerateOut:
    tok = _state.get("tokenizer")
    model = _state.get("model")
    base_id = _state.get("base_model_id")
    if tok is None or model is None or not base_id:
        raise HTTPException(503, "Model not loaded")

    row = body.model_dump()
    max_new = int(row.pop("max_new_tokens"))
    temperature = float(row.pop("temperature"))
    do_sample = bool(row.pop("do_sample"))

    if MODEL_PROFILE == "qwen":
        messages = _build_messages_qwen(row)
        add_gen = True
    else:
        messages = _build_messages_deepseek(row)
        add_gen = True

    prompt_text = tok.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=add_gen,
    )

    device = next(model.parameters()).device
    inputs = tok(
        prompt_text,
        return_tensors="pt",
        truncation=True,
        max_length=MAX_SEQ_LEN,
    )
    inputs = {k: v.to(device) for k, v in inputs.items()}

    gen_kwargs: dict[str, Any] = {
        "max_new_tokens": max_new,
        "do_sample": do_sample,
        "eos_token_id": tok.eos_token_id,
        "pad_token_id": tok.pad_token_id,
    }
    if do_sample:
        gen_kwargs["temperature"] = temperature
    else:
        # Greedy decode: neutral values so we do not inherit sampling defaults from model.config (HF warnings).
        gen_kwargs["temperature"] = 1.0
        gen_kwargs["top_p"] = 1.0
        gen_kwargs["top_k"] = 50

    with torch.no_grad():
        outputs = model.generate(**inputs, **gen_kwargs)

    new_ids = outputs[0][inputs["input_ids"].shape[1] :].tolist()
    gen = _decode_new_tokens(tok, new_ids)

    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return GenerateOut(
        text=gen,
        model_profile=MODEL_PROFILE,  # type: ignore[arg-type]
        base_model_id=base_id,
    )


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
