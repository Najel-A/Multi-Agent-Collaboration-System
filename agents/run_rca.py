#!/usr/bin/env python3
"""CLI to run the multi-agent RCA pipeline on synthetic incidents.

Usage:
  # Analyze first N incidents from source data
  python -m agents.run_rca --data data/02-raw/synthetic_source.jsonl --count 5

  # Analyze a single incident by ID
  python -m agents.run_rca --data data/02-raw/synthetic_source.jsonl --id <uuid>

  # Batch mode with evaluation against ground truth
  python -m agents.run_rca --data data/02-raw/synthetic_source.jsonl --count 100 --evaluate
"""

import argparse
import json
import sys
from pathlib import Path

from agents.orchestrator import Orchestrator


def load_incidents(path: str, count: int | None = None, incident_id: str | None = None) -> list[dict]:
    records: list[dict] = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if incident_id and rec.get("id") == incident_id:
                return [rec]
            records.append(rec)
            if count and len(records) >= count and not incident_id:
                break
    return records


def evaluate_report(report, ground_truth: dict) -> dict:
    """Compare RCA report against ground truth from synthetic data."""
    gt_category = ground_truth.get("remediation", {}).get("category", "")
    gt_diagnosis = ground_truth.get("remediation", {}).get("diagnosis", "")
    gt_scenario = ground_truth.get("fault", {}).get("scenario_id", "")

    # Category match (fuzzy — check if ground truth category appears in predicted)
    pred_cat = report.category.lower().replace("_", "")
    gt_cat = gt_category.lower().replace("_", "")
    category_match = gt_cat in pred_cat or pred_cat in gt_cat

    # Diagnosis keyword overlap
    gt_keywords = set(gt_diagnosis.lower().split())
    pred_keywords = set(report.root_cause.lower().split())
    common = gt_keywords & pred_keywords
    keyword_overlap = len(common) / max(len(gt_keywords), 1)

    return {
        "incident_id": report.incident_id,
        "scenario": gt_scenario,
        "category_match": category_match,
        "predicted_category": report.category,
        "ground_truth_category": gt_category,
        "keyword_overlap": round(keyword_overlap, 3),
        "has_fix_plan": len(report.fix_plan) > 0,
        "has_commands": len(report.commands) > 0,
    }


def main():
    ap = argparse.ArgumentParser(description="Run multi-agent RCA pipeline")
    ap.add_argument("--data", type=str, default="data/02-raw/synthetic_source.jsonl")
    ap.add_argument("--count", type=int, default=5, help="Number of incidents to analyze")
    ap.add_argument("--id", type=str, default=None, help="Analyze a specific incident by ID")
    ap.add_argument("--evaluate", action="store_true", help="Evaluate against ground truth")
    ap.add_argument("--output", type=str, default=None, help="Write reports to JSON file")
    ap.add_argument("--verbose", action="store_true", help="Print full reports")
    args = ap.parse_args()

    if not Path(args.data).exists():
        print(f"Error: {args.data} not found", file=sys.stderr)
        sys.exit(1)

    incidents = load_incidents(args.data, args.count, args.id)
    if not incidents:
        print("No incidents found.", file=sys.stderr)
        sys.exit(1)

    print(f"Analyzing {len(incidents)} incident(s)...\n")

    orchestrator = Orchestrator(parallel=True)
    reports = orchestrator.analyze_batch(incidents)

    eval_results: list[dict] = []

    for report, incident in zip(reports, incidents):
        if args.verbose:
            print(report.summary())
            print()
        else:
            print(f"[{report.incident_id[:8]}] {report.category} | {report.severity} | {report.root_cause[:80]}...")

        if args.evaluate:
            ev = evaluate_report(report, incident)
            eval_results.append(ev)

    # Evaluation summary
    if args.evaluate and eval_results:
        cat_matches = sum(1 for e in eval_results if e["category_match"])
        avg_overlap = sum(e["keyword_overlap"] for e in eval_results) / len(eval_results)
        print(f"\n=== Evaluation ({len(eval_results)} incidents) ===")
        print(f"Category accuracy: {cat_matches}/{len(eval_results)} ({100*cat_matches/len(eval_results):.1f}%)")
        print(f"Avg keyword overlap: {avg_overlap:.3f}")
        print(f"Fix plan coverage: {sum(1 for e in eval_results if e['has_fix_plan'])}/{len(eval_results)}")

    # Write output
    if args.output:
        output_data = [r.to_dict() for r in reports]
        if args.evaluate:
            for od, ev in zip(output_data, eval_results):
                od["evaluation"] = ev
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nReports written to {args.output}")


if __name__ == "__main__":
    main()
