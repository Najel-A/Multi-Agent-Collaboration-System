#!/usr/bin/env python3
"""Transform raw k8s_config_incidents.jsonl into simplified 4-field JSONL.

Source fields (02-raw/k8s_config_incidents.jsonl):
    scenario_id, evidence_text, diagnosis_text, fix_plan_text, ...

Target fields (02-raw/k8s_incidents_transformed.jsonl):
    scenario      — scenario_id
    k_describe_output — kubectl describe pod + events section from evidence_text
    k_logs_output     — container logs section from evidence_text
    solution          — combined diagnosis + fix plan as a single paragraph
"""

import json
import re
from pathlib import Path


RAW_DIR = Path(__file__).resolve().parent.parent / "02-raw"
INPUT_PATH = RAW_DIR / "k8s_config_incidents.jsonl"
OUTPUT_PATH = RAW_DIR / "k8s_incidents_transformed.jsonl"


def extract_describe_output(evidence: str) -> str:
    """Extract the kubectl describe pod section (including events) up to the
    kubectl get events or container logs delimiter."""
    # Start after "=== kubectl describe pod ==="
    m_start = re.search(r"=== kubectl describe pod ===\n", evidence)
    if not m_start:
        return ""
    start = m_start.end()

    # End at the next section delimiter
    m_end = re.search(r"\n=== kubectl get events ===", evidence[start:])
    if m_end:
        return evidence[start : start + m_end.start()].strip()

    # Fallback: end at container logs
    m_end = re.search(r"\n=== container logs ===", evidence[start:])
    if m_end:
        return evidence[start : start + m_end.start()].strip()

    return evidence[start:].strip()


def extract_logs_output(evidence: str) -> str:
    """Extract the container logs section."""
    m = re.search(r"=== container logs ===\n", evidence)
    if not m:
        return ""
    return evidence[m.end():].strip()


def build_solution(diagnosis: str, fix_plan: str) -> str:
    """Combine diagnosis and fix plan into a single solution paragraph."""
    # Strip numbered prefixes from fix_plan steps and join into flowing text
    steps = []
    for line in fix_plan.strip().splitlines():
        line = re.sub(r"^\d+\.\s*", "", line.strip())
        if line:
            steps.append(line)
    fix_text = " ".join(steps)
    # Combine: diagnosis sentence(s) + fix steps
    return f"{diagnosis.strip()} {fix_text}".strip()


def main():
    records_in = 0
    records_out = 0

    with open(INPUT_PATH, "r") as fin, open(OUTPUT_PATH, "w") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            records_in += 1
            rec = json.loads(line)

            transformed = {
                "scenario": rec["scenario_id"],
                "k_describe_output": extract_describe_output(rec["evidence_text"]),
                "k_logs_output": extract_logs_output(rec["evidence_text"]),
                "solution": build_solution(
                    rec["diagnosis_text"], rec["fix_plan_text"]
                ),
            }
            fout.write(json.dumps(transformed, ensure_ascii=False) + "\n")
            records_out += 1

    print(f"Transformed {records_in} → {records_out} records")
    print(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
