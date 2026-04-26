#!/usr/bin/env python3
"""Generate model-specific SFT JSONL files for each agent role.

Agent roles & models:
  RCA           → qwen3.5:9b, deepseek-r1:8b
  Executor      → devstral-small-2:24b
  Validator     → qwen3.5:35b, llama3.2:3b

Source: data/02-raw/k8s_config_incidents.jsonl  (granular fields)
Output: data/sft/<role>_<model>.jsonl            (chat-formatted text)

Each output record: {"text": "<chat-formatted string>"}
"""

import json
import re
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parent.parent / "02-raw"
SFT_DIR = Path(__file__).resolve().parent.parent / "sft"
INPUT_PATH = RAW_DIR / "k8s_config_incidents.jsonl"

# ---------------------------------------------------------------------------
# Evidence extraction helpers (from evidence_text)
# ---------------------------------------------------------------------------

def extract_describe(evidence: str) -> str:
    m = re.search(r"=== kubectl describe pod ===\n", evidence)
    if not m:
        return ""
    start = m.end()
    m2 = re.search(r"\n=== kubectl get events ===", evidence[start:])
    end = start + m2.start() if m2 else len(evidence)
    return evidence[start:end].strip()


def extract_logs(evidence: str) -> str:
    m = re.search(r"=== container logs ===\n", evidence)
    if not m:
        return ""
    return evidence[m.end():].strip()


def extract_events(evidence: str) -> str:
    m = re.search(r"=== kubectl get events ===\n", evidence)
    if not m:
        return ""
    start = m.end()
    m2 = re.search(r"\n=== container logs ===", evidence[start:])
    end = start + m2.start() if m2 else len(evidence)
    return evidence[start:end].strip()


# ---------------------------------------------------------------------------
# System prompts per role
# ---------------------------------------------------------------------------

SYSTEM_RCA = (
    "You are a Kubernetes Root Cause Analysis (RCA) agent. "
    "Given kubectl describe output and container logs from a failing pod, "
    "identify the root cause of the incident. Provide a clear, concise diagnosis "
    "explaining what is wrong and why the pod is in its current state."
)

SYSTEM_EXECUTOR = (
    "You are a Kubernetes Executor agent. "
    "Given an incident diagnosis and fix plan, produce the exact kubectl commands "
    "and actions needed to remediate the issue. Commands must be safe, ordered, "
    "and copy-pasteable."
)

SYSTEM_VALIDATOR = (
    "You are a Kubernetes Validation agent. "
    "Given the actions taken to remediate an incident, produce verification steps "
    "to confirm the fix worked and rollback guidance if the fix causes new issues."
)


# ---------------------------------------------------------------------------
# Chat template formatters
# ---------------------------------------------------------------------------

def fmt_chatml(system: str, user: str, assistant: str) -> str:
    """ChatML format — Qwen models."""
    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n{assistant}<|im_end|>"
    )


def fmt_deepseek_r1(system: str, user: str, assistant: str) -> str:
    """DeepSeek-R1 format."""
    return (
        f"<|begin▁of▁sentence|>"
        f"<|System|>\n{system}\n"
        f"<|User|>\n{user}\n"
        f"<|Assistant|>\n<think>\n\n</think>\n\n{assistant}"
        f"<|end▁of▁sentence|>"
    )


def fmt_mistral(system: str, user: str, assistant: str) -> str:
    """Mistral/Devstral instruct format."""
    return (
        f"[INST] {system}\n\n{user} [/INST]\n{assistant}</s>"
    )


def fmt_llama3(system: str, user: str, assistant: str) -> str:
    """Llama 3.x instruct format."""
    return (
        f"<|begin_of_text|>"
        f"<|start_header_id|>system<|end_header_id|>\n\n{system}<|eot_id|>"
        f"<|start_header_id|>user<|end_header_id|>\n\n{user}<|eot_id|>"
        f"<|start_header_id|>assistant<|end_header_id|>\n\n{assistant}<|eot_id|>"
    )


# ---------------------------------------------------------------------------
# Role-specific prompt/completion builders
# ---------------------------------------------------------------------------

def build_rca(rec: dict) -> tuple[str, str]:
    """Returns (user_prompt, assistant_completion) for RCA role."""
    evidence = rec["evidence_text"]
    describe = extract_describe(evidence)
    logs = extract_logs(evidence)

    user = (
        f"Analyze this Kubernetes incident and identify the root cause.\n\n"
        f"## kubectl describe pod\n```\n{describe}\n```\n\n"
        f"## Container logs\n```\n{logs}\n```"
    )
    assistant = rec["diagnosis_text"].strip()
    return user, assistant


def build_executor(rec: dict) -> tuple[str, str]:
    """Returns (user_prompt, assistant_completion) for Executor role."""
    evidence = rec["evidence_text"]
    describe = extract_describe(evidence)

    user = (
        f"Given the following incident context and fix plan, "
        f"provide the exact commands to remediate.\n\n"
        f"## Incident context\n```\n{describe}\n```\n\n"
        f"## Diagnosis\n{rec['diagnosis_text'].strip()}\n\n"
        f"## Fix plan\n{rec['fix_plan_text'].strip()}"
    )
    assistant = rec["actions_text"].strip()
    return user, assistant


def build_validator(rec: dict) -> tuple[str, str]:
    """Returns (user_prompt, assistant_completion) for Validator role."""
    evidence = rec["evidence_text"]
    describe = extract_describe(evidence)

    user = (
        f"Given the remediation actions taken for this incident, "
        f"provide verification steps and rollback guidance.\n\n"
        f"## Incident context\n```\n{describe}\n```\n\n"
        f"## Diagnosis\n{rec['diagnosis_text'].strip()}\n\n"
        f"## Actions taken\n{rec['actions_text'].strip()}"
    )
    verification = rec["verification_text"].strip()
    rollback = rec["rollback_text"].strip()
    assistant = f"## Verification\n{verification}\n\n## Rollback\n{rollback}"
    return user, assistant


# ---------------------------------------------------------------------------
# Output specs: (filename, role_builder, system_prompt, formatter)
# ---------------------------------------------------------------------------

OUTPUTS = [
    # RCA agents
    ("rca_qwen3_5_9b.jsonl",       build_rca,       SYSTEM_RCA,       fmt_chatml),
    ("rca_deepseek_r1_8b.jsonl",   build_rca,       SYSTEM_RCA,       fmt_deepseek_r1),
    # Executor agent
    ("executor_devstral_24b.jsonl", build_executor,  SYSTEM_EXECUTOR,  fmt_mistral),
    # Validator agents
    ("validator_qwen3_5_35b.jsonl", build_validator, SYSTEM_VALIDATOR, fmt_chatml),
    ("validator_llama3_2_3b.jsonl", build_validator, SYSTEM_VALIDATOR, fmt_llama3),
]


def main():
    # Load all records
    records = []
    with open(INPUT_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))

    print(f"Loaded {len(records)} records from {INPUT_PATH.name}\n")

    SFT_DIR.mkdir(parents=True, exist_ok=True)

    for filename, role_fn, system, formatter in OUTPUTS:
        out_path = SFT_DIR / filename
        count = 0
        skipped = 0
        with open(out_path, "w") as fout:
            for rec in records:
                user, assistant = role_fn(rec)
                # Skip records with empty completions
                if not assistant:
                    skipped += 1
                    continue
                text = formatter(system, user, assistant)
                fout.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
                count += 1

        status = f"  {filename:<40s}  {count:5d} records"
        if skipped:
            status += f"  ({skipped} skipped)"
        print(status)

    print(f"\nOutput directory: {SFT_DIR}")


if __name__ == "__main__":
    main()
