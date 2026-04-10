#!/usr/bin/env python3
"""
Project collect_k8s_incidents JSONL rows onto the collaborator schema (one object per line):

  scenario_id, namespace, workload_kind, workload_name, container_name, image,
  created_at, pod_status, waiting_reason, evidence_text,
  diagnosis_text, fix_plan_text, actions_text, verification_text, rollback_text

Same key set as samples like failedscheduling_insufficient_memory; scenario_id values
come from each row (e.g. crashloop_bad_args, oomkilled_limit_too_low, ...).

By default, rewrites the evidence_text header to the friend's four-line form
(namespace / workload / container / image) and keeps everything from the first
\"=== kubectl get pods ===\" onward so it merges cleanly with their files.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Order matches typical friend exports (stable for diffs / concatenation).
FRIEND_KEYS: List[str] = [
    "scenario_id",
    "namespace",
    "workload_kind",
    "workload_name",
    "container_name",
    "image",
    "created_at",
    "pod_status",
    "waiting_reason",
    "evidence_text",
    "diagnosis_text",
    "fix_plan_text",
    "actions_text",
    "verification_text",
    "rollback_text",
]

ANCHOR = "=== kubectl get pods ==="


def friend_evidence_text(row: Dict[str, Any]) -> str:
    """Four-line header like friend's sample; body from original evidence after kubectl sections."""
    ns = str(row.get("namespace") or "")
    wl = str(row.get("workload_name") or "")
    cn = str(row.get("container_name") or "")
    img = str(row.get("image") or "")
    header = (
        f"namespace: {ns}\n"
        f"workload: {wl}\n"
        f"container: {cn}\n"
        f"image: {img}\n"
    )
    et = row.get("evidence_text")
    if not isinstance(et, str) or not et.strip():
        return header.rstrip("\n")
    pos = et.find(ANCHOR)
    if pos == -1:
        return (header + et.lstrip("\n")).rstrip("\n")
    # One newline after image line, then kubectl sections (matches friend's evidence_text).
    return (header + et[pos:]).rstrip("\n")


def friend_actions_text(actions: str) -> str:
    """Friend samples use `kubectl -n ...` lines without a [kubectl] prefix."""
    if not actions:
        return ""
    lines = []
    for line in actions.splitlines():
        s = line.strip()
        if s.startswith("[kubectl] "):
            s = s[len("[kubectl] ") :]
        lines.append(s)
    return "\n".join(lines).rstrip("\n")


def to_friend_row(
    row: Dict[str, Any],
    *,
    normalize_evidence: bool,
    normalize_actions: bool,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in FRIEND_KEYS:
        if k == "evidence_text" and normalize_evidence:
            out[k] = friend_evidence_text(row)
        else:
            v = row.get(k)
            if v is None:
                out[k] = ""
            elif k == "waiting_reason" and v == "":
                out[k] = ""
            else:
                out[k] = v
    if normalize_actions and isinstance(out.get("actions_text"), str):
        out["actions_text"] = friend_actions_text(out["actions_text"])
    return out


def main() -> None:
    p = argparse.ArgumentParser(
        description="Export JSONL to collaborator 15-field schema (friend-style rows)."
    )
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument(
        "--no-normalize-evidence",
        action="store_true",
        help="Keep evidence_text exactly as in the source row (including workload_kind line).",
    )
    p.add_argument(
        "--no-normalize-actions",
        action="store_true",
        help="Keep actions_text exactly as in the source (including [kubectl] prefixes).",
    )
    p.add_argument(
        "--per-scenario",
        type=int,
        default=None,
        metavar="N",
        help="Emit at most N rows per scenario_id (order follows the input file).",
    )
    p.add_argument(
        "--only-scenarios",
        type=str,
        default=None,
        help="Comma-separated scenario_id values to include (default: all).",
    )
    args = p.parse_args()
    normalize_evidence = not args.no_normalize_evidence
    normalize_actions = not args.no_normalize_actions

    only: Optional[Set[str]] = None
    if args.only_scenarios:
        only = {s.strip() for s in args.only_scenarios.split(",") if s.strip()}

    per_counts: Dict[str, int] = defaultdict(int)
    n = 0
    with args.input.open(encoding="utf-8") as inf, args.output.open("w", encoding="utf-8") as outf:
        for line in inf:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            sid = str(row.get("scenario_id") or "")
            if only is not None and sid not in only:
                continue
            if args.per_scenario is not None:
                if per_counts[sid] >= args.per_scenario:
                    continue
                per_counts[sid] += 1
            outf.write(
                json.dumps(
                    to_friend_row(
                        row,
                        normalize_evidence=normalize_evidence,
                        normalize_actions=normalize_actions,
                    ),
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
    print(f"Wrote {n} rows to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
