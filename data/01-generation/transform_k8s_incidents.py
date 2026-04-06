#!/usr/bin/env python3
"""Transform raw k8s_config_incidents.jsonl into 4-column JSONL.

Source fields (02-raw/k8s_config_incidents.jsonl):
    scenario_id, evidence_text, ...

Target fields (02-raw/k8s_incidents_transformed.jsonl):
    scenario_id       — incident scenario identifier
    pod_describe      — kubectl describe pod section from evidence_text
    pod_logs          — container logs section from evidence_text
    pod_logs_previous — previous container logs (empty if not present)
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
    m_start = re.search(r"=== kubectl describe pod ===\n", evidence)
    if not m_start:
        return ""
    start = m_start.end()

    # End at the next section delimiter
    m_end = re.search(r"\n=== kubectl get events ===", evidence[start:])
    if m_end:
        return evidence[start : start + m_end.start()].strip()

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


def extract_previous_logs(evidence: str) -> str:
    """Extract the previous container logs section, if present."""
    m = re.search(r"=== container logs previous ===\n", evidence)
    if not m:
        return ""
    return evidence[m.end():].strip()


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
                "scenario_id": rec["scenario_id"],
                "pod_describe": extract_describe_output(rec["evidence_text"]),
                "pod_logs": extract_logs_output(rec["evidence_text"]),
                "pod_logs_previous": extract_previous_logs(rec["evidence_text"]),
            }
            fout.write(json.dumps(transformed, ensure_ascii=False) + "\n")
            records_out += 1

    print(f"Transformed {records_in} → {records_out} records")
    print(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
