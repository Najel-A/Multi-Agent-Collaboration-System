#!/usr/bin/env python3
"""CLI to run the multi-agent RCA pipeline on k8s incidents.

Usage:
    # Default: bootstrap mode (one model for all 4 roles) against local Ollama
    python -m agents.run_rca --count 5

    # Production mode: per-role fine-tuned models
    python -m agents.run_rca --mode roles --count 5

    # Single incident by pod_name or scenario_id
    python -m agents.run_rca --id busybox-pn3zirfh-86bbb7957c-7754l

    # Point at vLLM / OpenAI-compatible endpoint
    python -m agents.run_rca --backend vllm --backend-url http://vllm.internal:8000/v1

    # Change bootstrap model
    python -m agents.run_rca --bootstrap-model llama3.2:3b

    # Write structured results to JSON
    python -m agents.run_rca --count 20 --output results.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

from agents.orchestrator import Orchestrator, StructuredRCAResult
from agents.model_loaders import ollama_loader, vllm_loader


DEFAULT_DATA = "data/02-raw/k8s_combined_incidents.jsonl"


def load_incidents(
    path: str,
    count: int | None = None,
    incident_id: str | None = None,
) -> list[dict]:
    """Load records from k8s_combined_incidents.jsonl (flat schema).

    --id matches against `pod_name` first, then `scenario_id`.
    """
    records: list[dict] = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if incident_id and (
                rec.get("pod_name") == incident_id
                or rec.get("scenario_id") == incident_id
            ):
                return [rec]
            if incident_id:
                continue
            records.append(rec)
            if count and len(records) >= count:
                break
    return records


def build_loader(args: argparse.Namespace):
    """Pick the model-client loader based on --backend."""
    if args.backend == "ollama":
        return ollama_loader(base_url=args.backend_url or "http://localhost:11434")
    if args.backend == "vllm":
        if not args.backend_url:
            print("--backend vllm requires --backend-url", file=sys.stderr)
            sys.exit(2)
        return vllm_loader(
            base_url=args.backend_url,
            api_key=os.environ.get("VLLM_API_KEY"),
        )
    raise ValueError(f"unknown backend: {args.backend}")


def build_orchestrator(args: argparse.Namespace) -> Orchestrator:
    loader = build_loader(args)
    if args.mode == "bootstrap":
        return Orchestrator.from_bootstrap(
            loader,
            model=args.bootstrap_model,
            parallel=not args.no_parallel,
        )
    return Orchestrator.from_role_defaults(loader, parallel=not args.no_parallel)


def print_short(r: StructuredRCAResult) -> None:
    head = r.incident_id[:40]
    diag = (r.diagnosis or "").replace("\n", " ")[:100]
    print(
        f"[{head:<40}] "
        f"fix_plan={len(r.fix_plan)} commands={len(r.commands)} "
        f"verification={len(r.verification)} rollback={len(r.rollback)}  "
        f"{diag}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Run multi-agent RCA pipeline")
    ap.add_argument("--data", type=str, default=DEFAULT_DATA,
                    help=f"Path to incidents JSONL (default: {DEFAULT_DATA})")
    ap.add_argument("--count", type=int, default=5,
                    help="Number of incidents to analyze")
    ap.add_argument("--id", type=str, default=None,
                    help="Analyze a single incident by pod_name or scenario_id")
    ap.add_argument("--mode", choices=("bootstrap", "roles"), default="bootstrap",
                    help="bootstrap = one model for all 4 slots (default); "
                         "roles = per-role fine-tuned models")
    ap.add_argument("--bootstrap-model", type=str, default="qwen3.5:9b",
                    help="Model used for all slots in bootstrap mode")
    ap.add_argument("--backend", choices=("ollama", "vllm"), default="ollama",
                    help="Inference backend")
    ap.add_argument("--backend-url", type=str, default=None,
                    help="Backend base URL (defaults to localhost Ollama)")
    ap.add_argument("--no-parallel", action="store_true",
                    help="Disable parallel Agent 1 / Agent 2 execution")
    ap.add_argument("--output", type=str, default=None,
                    help="Write structured results to JSON file")
    ap.add_argument("--verbose", action="store_true",
                    help="Print the full summary for each incident")
    args = ap.parse_args()

    if not Path(args.data).exists():
        print(f"Error: {args.data} not found", file=sys.stderr)
        sys.exit(1)

    incidents = load_incidents(args.data, args.count, args.id)
    if not incidents:
        print("No incidents found.", file=sys.stderr)
        sys.exit(1)

    print(
        f"Analyzing {len(incidents)} incident(s) "
        f"via {args.backend}/{args.mode}"
        + (f" [bootstrap model: {args.bootstrap_model}]" if args.mode == "bootstrap" else "")
        + "...\n"
    )

    orchestrator = build_orchestrator(args)
    results = orchestrator.analyze_batch(incidents)

    for r in results:
        if args.verbose:
            print(r.summary())
            print()
        else:
            print_short(r)

    if args.output:
        with open(args.output, "w") as f:
            json.dump([r.to_dict() for r in results], f, indent=2)
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
