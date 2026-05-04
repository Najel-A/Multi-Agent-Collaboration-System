from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


def _get(row: Mapping[str, Any], key: str, default: str = "") -> str:
    val = row.get(key, default)
    if val is None:
        return default
    return str(val)


@dataclass(frozen=True)
class ClassificationPromptConfig:
    system_prompt: str = (
        "You are a Kubernetes incident classification assistant. "
        "Given incident evidence, predict the scenario_id label."
    )
    instruction: str = "Return only the scenario_id."
    evidence_key: str = "evidence_text"


def build_classification_prompt(
    row: Mapping[str, Any],
    cfg: ClassificationPromptConfig = ClassificationPromptConfig(),
) -> str:
    evidence = _get(row, cfg.evidence_key, "")
    return (
        f"System: {cfg.system_prompt}\n"
        f"Instruction: {cfg.instruction}\n\n"
        f"Evidence:\n{evidence}\n"
    )


@dataclass(frozen=True)
class RemediationPromptConfig:
    system_prompt: str = (
        "You are a Kubernetes Site Reliability Engineering (SRE) assistant. "
        "Given raw incident evidence (kubectl outputs, events, logs), produce a "
        "clear remediation response."
    )
    instruction: str = (
        "Provide: Diagnosis, Fix Plan, Actions (commands), Verification, Rollback."
    )
    evidence_key: str = "evidence_text"


def build_generation_prompt(
    row: Mapping[str, Any],
    cfg: RemediationPromptConfig = RemediationPromptConfig(),
) -> str:
    evidence = _get(row, cfg.evidence_key, "")
    return (
        f"System: {cfg.system_prompt}\n"
        f"Instruction: {cfg.instruction}\n\n"
        f"Incident Evidence:\n{evidence}\n\n"
        "Response:\n"
    )


def format_remediation_target(
    row: Mapping[str, Any],
    *,
    diagnosis_key: str = "diagnosis_text",
    fix_plan_key: str = "fix_plan_text",
    actions_key: str = "actions_text",
    verification_key: str = "verification_text",
    rollback_key: str = "rollback_text",
    include_empty_sections: bool = False,
) -> str:
    """
    Standardize the remediation target into a single instruction-style output.
    Easy to extend later with new sections (e.g., 'Risks', 'Escalation', etc.).
    """
    sections: list[tuple[str, str]] = [
        ("Diagnosis", _get(row, diagnosis_key, "")),
        ("Fix Plan", _get(row, fix_plan_key, "")),
        ("Actions", _get(row, actions_key, "")),
        ("Verification", _get(row, verification_key, "")),
        ("Rollback", _get(row, rollback_key, "")),
    ]

    out: list[str] = []
    for title, body in sections:
        body = body.strip()
        if not body and not include_empty_sections:
            continue
        out.append(f"## {title}\n{body}".rstrip())
    return "\n\n".join(out).strip()


def build_chatml_text(
    *,
    system_prompt: str,
    user_prompt: str,
    assistant_text: Optional[str] = None,
) -> str:
    """
    Generic ChatML text builder (compatible with Qwen-style training text).
    Keep this generic so you can plug different base models later.
    """
    if assistant_text is None:
        assistant_text = ""
    return (
        f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
        f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
        f"<|im_start|>assistant\n{assistant_text}<|im_end|>"
    )

