"""
Data transformations for multi-agent Kubernetes RCA on synthetic incident JSONL.

We produce TWO derived datasets from the same raw incidents:

- Agent 1 (structured RCA agent):
  - Fast, structured classification of root cause / scenario.
  - Flattened columns + engineered features.
  - Intended for models like RandomForest / XGBoost / LightGBM.

- Agent 2 (semantic / evidence agent):
  - Text-based RCA and remediation generation.
  - Combined evidence text + labeled diagnosis/fix text.
  - Intended for embeddings, retrieval, or LLM / RAG pipelines.

This supports a multi-agent RCA architecture where:
- Agent 1 answers: "What failure type is this based on structured evidence?"
- Agent 2 answers: "What does this incident mean and how should it be fixed?"
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def load_incidents(path: str | Path) -> pd.DataFrame:
    """
    Load incident JSONL into a pandas DataFrame.

    Each line is a JSON object with keys:
      id, context, world, fault, observations, remediation, meta
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    df = pd.read_json(p, lines=True)
    return df


def flatten_incidents(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Flatten nested incident JSON into a wide DataFrame using json_normalize.

    We keep dot-separated paths such as:
      context.cluster_id, fault.scenario_id, observations.kubectl_describe_pod, ...
    """
    # json_normalize will handle dicts / nested objects per row
    records: List[Dict[str, Any]] = df_raw.to_dict(orient="records")
    flat = pd.json_normalize(records)
    return flat


def write_parquet(df: pd.DataFrame, path: str | Path) -> None:
    """Write DataFrame to Parquet (no index)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


# ---------------------------------------------------------------------------
# Parsing helpers for Kubernetes text signals
# ---------------------------------------------------------------------------

_RE_STATUS_LINE = re.compile(r"^Status:\s*(.+)$", re.MULTILINE)
_RE_WAITING_REASON = re.compile(r"^\s*Reason:\s*(.+)$", re.MULTILINE)
_RE_MESSAGE = re.compile(r"^\s*Message:\s*(.+)$", re.MULTILINE)
_RE_RESTART_COUNT = re.compile(r"^\s*Restart Count:\s*(\d+)\s*$", re.MULTILINE)


def parse_describe_pod(text: Optional[str]) -> Dict[str, Any]:
    """
    Parse kubectl_describe_pod text into structured fields.

    Extracts:
      - pod_status
      - waiting_reason
      - error_message
      - restart_count  (int or None)
    """
    if not text:
        return {
            "pod_status": None,
            "waiting_reason": None,
            "error_message": None,
            "restart_count": None,
        }
    status = _RE_STATUS_LINE.search(text)
    waiting_reason = _RE_WAITING_REASON.search(text)
    message = _RE_MESSAGE.search(text)
    restart = _RE_RESTART_COUNT.search(text)
    return {
        "pod_status": status.group(1).strip() if status else None,
        "waiting_reason": waiting_reason.group(1).strip() if waiting_reason else None,
        "error_message": message.group(1).strip() if message else None,
        "restart_count": int(restart.group(1)) if restart else None,
    }


def parse_events(text: Optional[str]) -> Dict[str, Any]:
    """
    Parse kubectl_get_events table into a single representative event.

    Expected format:
      LAST SEEN<TAB>TYPE<TAB>REASON<TAB>OBJECT<TAB>MESSAGE
      22s   <TAB>Warning<TAB>Failed<TAB>pod/...<TAB>Error: ...

    We take the first non-empty row after the header.
    """
    if not text:
        return {"event_type": None, "event_reason": None, "event_message": None}

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {"event_type": None, "event_reason": None, "event_message": None}

    # Skip header line(s) that contain 'LAST SEEN' or 'TYPE'
    data_lines: List[str] = []
    for ln in lines:
        if "LAST SEEN" in ln and "TYPE" in ln and "REASON" in ln:
            continue
        data_lines.append(ln)
    if not data_lines:
        return {"event_type": None, "event_reason": None, "event_message": None}

    # Use first event row
    first = data_lines[0]
    parts = first.split("\t")
    if len(parts) < 5:
        # Try space split as fallback
        parts = re.split(r"\s+", first, maxsplit=4)
        if len(parts) < 5:
            return {"event_type": None, "event_reason": None, "event_message": first.strip()}

    # parts: [LAST SEEN, TYPE, REASON, OBJECT, MESSAGE]
    return {
        "event_type": parts[1].strip() if len(parts) > 1 else None,
        "event_reason": parts[2].strip() if len(parts) > 2 else None,
        "event_message": parts[4].strip() if len(parts) > 4 else None,
    }


def parse_metrics_snapshot(ms: Any) -> Dict[str, Any]:
    """
    Extract simple fields from metrics_snapshot dict.

    Expected keys:
      - restarts (int)
      - oom_killed (bool)
    """
    if not isinstance(ms, dict):
        return {"restart_count_metrics": None, "oom_killed": None}
    return {
        "restart_count_metrics": ms.get("restarts"),
        "oom_killed": ms.get("oom_killed"),
    }


# ---------------------------------------------------------------------------
# Feature engineering for Agent 1
# ---------------------------------------------------------------------------


def derive_root_cause_family(
    scenario_id: Optional[str],
    variant: Optional[str],
    fault_params: Dict[str, Any] | None,
) -> str:
    """
    Map detailed scenario/variant into a coarse root cause family label.
    """
    s_id = (scenario_id or "").lower()
    var = (variant or "").lower()
    params = fault_params or {}

    if "missing_secret" in s_id or "secret_not_found" in var or "missing_secret" in params:
        return "missing_secret"
    if "configmap" in s_id:
        return "configmap"
    if "imagepull" in s_id or "image_pull" in s_id:
        return "image_pull"
    if "failedscheduling" in s_id or "nodeselector" in s_id:
        return "scheduling"
    if "pvc_" in s_id or "pvc" in s_id:
        return "pvc"
    if "oom" in s_id:
        return "oom"
    if "probe" in s_id:
        return "probe"
    if "rbac" in s_id or "forbidden" in s_id:
        return "rbac"
    if "dns" in s_id:
        return "dns"
    if "connection_refused" in s_id or "service_connection" in s_id:
        return "connection"
    if "quota" in s_id:
        return "quota"
    if "gitops" in s_id:
        return "gitops"
    return "other"


def derive_symptom_family(
    category: Optional[str],
    pod_status: Optional[str],
    waiting_reason: Optional[str],
) -> str:
    """
    Map raw status / category into a coarse symptom family (what the user sees).
    """
    cat = (category or "").lower()
    status = (pod_status or "").lower()
    reason = (waiting_reason or "").lower()

    text = " ".join([cat, status, reason])
    if "createcontainerconfigerror" in text:
        return "CreateContainerConfigError"
    if "imagepullbackoff" in text:
        return "ImagePullBackOff"
    if "crashloopbackoff" in text:
        return "CrashLoopBackOff"
    if "failedscheduling" in text:
        return "FailedScheduling"
    if "pending" in text:
        return "Pending"
    if "running" in text and "notready" in text:
        return "RunningNotReady"
    return category or pod_status or waiting_reason or "unknown"


def build_agent1_structured(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Build the Agent 1 (structured RCA) dataset from raw incidents.

    Returns a DataFrame with columns suitable for tree-based models, including
    flattened context/fault/meta fields, parsed observation fields, and
    derived feature families.
    """
    flat = flatten_incidents(df_raw)

    # Convenience aliases (handle missing columns defensively)
    def col(name: str) -> pd.Series:
        return flat[name] if name in flat.columns else pd.Series([None] * len(flat))

    # Parse observation text fields
    desc_parsed = flat.get("observations.kubectl_describe_pod", pd.Series([None] * len(flat))).apply(
        parse_describe_pod
    )
    desc_df = pd.DataFrame(desc_parsed.tolist())

    events_parsed = flat.get("observations.kubectl_get_events", pd.Series([None] * len(flat))).apply(
        parse_events
    )
    events_df = pd.DataFrame(events_parsed.tolist())

    metrics_parsed = flat.get("observations.metrics_snapshot", pd.Series([None] * len(flat))).apply(
        parse_metrics_snapshot
    )
    metrics_df = pd.DataFrame(metrics_parsed.tolist())

    # Fault params
    fp_series = flat.get("fault.fault_params", pd.Series([None] * len(flat)))
    fault_params_df = pd.DataFrame(
        [
            (fp or {})
            if isinstance(fp, dict)
            else {}
            for fp in fp_series
        ]
    )

    # Root cause / symptom families
    root_fam = [
        derive_root_cause_family(
            flat.get("fault.scenario_id", pd.Series([None] * len(flat))).iat[i]
            if "fault.scenario_id" in flat.columns
            else None,
            flat.get("fault.variant", pd.Series([None] * len(flat))).iat[i]
            if "fault.variant" in flat.columns
            else None,
            fp_series.iat[i] if i < len(fp_series) else None,
        )
        for i in range(len(flat))
    ]

    symptom_fam = [
        derive_symptom_family(
            flat.get("remediation.category", pd.Series([None] * len(flat))).iat[i]
            if "remediation.category" in flat.columns
            else None,
            desc_df["pod_status"].iat[i] if i < len(desc_df) else None,
            desc_df["waiting_reason"].iat[i] if i < len(desc_df) else None,
        )
        for i in range(len(flat))
    ]

    # Final Agent 1 schema
    out = pd.DataFrame(
        {
            "id": col("id"),
            "cluster_id": col("context.cluster_id"),
            "namespace": col("context.namespace"),
            "workload_kind": col("context.workload_kind"),
            "workload_name": col("context.workload_name"),
            "container_name": col("context.container_name"),
            "image": col("context.image"),
            "scenario_id": col("fault.scenario_id"),
            "variant": col("fault.variant"),
            "category": col("remediation.category"),
            "created_at": col("meta.created_at"),
            "difficulty": col("meta.difficulty"),
            "noise_level": col("meta.noise_level"),
            "failure_phase": col("meta.failure_phase"),
            # Parsed fields from describe/events/metrics
            "pod_status": desc_df["pod_status"],
            "waiting_reason": desc_df["waiting_reason"],
            "error_message": desc_df["error_message"],
            "restart_count": desc_df["restart_count"],
            "event_type": events_df["event_type"],
            "event_reason": events_df["event_reason"],
            "event_message": events_df["event_message"],
            "restart_count_metrics": metrics_df["restart_count_metrics"],
            "oom_killed": metrics_df["oom_killed"],
            # Fault params (if present)
            "missing_secret": fault_params_df.get("missing_secret"),
            "missing_key": fault_params_df.get("missing_key"),
            # Derived labels
            "symptom_family": symptom_fam,
            "root_cause_family": root_fam,
        }
    )

    return out


# ---------------------------------------------------------------------------
# Agent 2 (semantic / evidence) transformations
# ---------------------------------------------------------------------------


def _summarize_metrics(ms: Any) -> str:
    """Turn metrics_snapshot into a short textual summary."""
    if not isinstance(ms, dict):
        return ""
    parts: List[str] = []
    if "restarts" in ms:
        parts.append(f"restarts={ms['restarts']}")
    if "oom_killed" in ms:
        parts.append(f"oom_killed={ms['oom_killed']}")
    return "metrics_snapshot: " + ", ".join(parts) if parts else ""


def build_evidence_text(row: pd.Series) -> str:
    """
    Combine key context + observation fields into a single evidence_text string.
    """
    ns = (row.get("context", {}) or {}).get("namespace") if isinstance(row.get("context"), dict) else None
    workload = (row.get("context", {}) or {}).get("workload_name") if isinstance(row.get("context"), dict) else None
    container = (row.get("context", {}) or {}).get("container_name") if isinstance(row.get("context"), dict) else None
    image = (row.get("context", {}) or {}).get("image") if isinstance(row.get("context"), dict) else None

    obs = row.get("observations") or {}
    if not isinstance(obs, dict):
        obs = {}

    header_lines = [
        f"namespace: {ns}" if ns else "",
        f"workload: {workload}" if workload else "",
        f"container: {container}" if container else "",
        f"image: {image}" if image else "",
    ]
    header = "\n".join([ln for ln in header_lines if ln])

    sections = [
        header,
        "=== kubectl get pods ===",
        (obs.get("kubectl_get_pods") or "").rstrip(),
        "=== kubectl describe pod ===",
        (obs.get("kubectl_describe_pod") or "").rstrip(),
        "=== kubectl get events ===",
        (obs.get("kubectl_get_events") or "").rstrip(),
        "=== container logs ===",
        (obs.get("container_logs") or "").rstrip(),
    ]

    metrics_txt = _summarize_metrics(obs.get("metrics_snapshot"))
    if metrics_txt:
        sections.append("=== metrics_snapshot ===")
        sections.append(metrics_txt)

    return "\n".join(sections).strip()


def _join_list(lines: Any, sep: str = "\n") -> str:
    if not isinstance(lines, list):
        return ""
    return sep.join(str(x) for x in lines)


def flatten_actions_structured(actions: Any) -> str:
    """
    Flatten remediation.actions_structured into readable text.
    Each action becomes a line like:
      [type] cmd
    """
    if not isinstance(actions, list):
        return ""
    lines: List[str] = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        t = a.get("type") or "action"
        cmd = a.get("cmd") or ""
        lines.append(f"[{t}] {cmd}".strip())
    return "\n".join(lines)


def build_agent2_evidence(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Build the Agent 2 (semantic / evidence) dataset from raw incidents.

    Produces fields:
      id, scenario_id, variant, category,
      namespace, workload_name, container_name, image,
      difficulty, noise_level, failure_phase,
      evidence_text, diagnosis_text, fix_plan_text,
      actions_text, verification_text, rollback_text
    """
    records: List[Dict[str, Any]] = []
    for _, row in df_raw.iterrows():
        ctx = row.get("context") or {}
        if not isinstance(ctx, dict):
            ctx = {}
        fault = row.get("fault") or {}
        if not isinstance(fault, dict):
            fault = {}
        rem = row.get("remediation") or {}
        if not isinstance(rem, dict):
            rem = {}
        meta = row.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}

        record: Dict[str, Any] = {
            "id": row.get("id"),
            "scenario_id": fault.get("scenario_id"),
            "variant": fault.get("variant"),
            "category": rem.get("category"),
            "namespace": ctx.get("namespace"),
            "workload_name": ctx.get("workload_name"),
            "container_name": ctx.get("container_name"),
            "image": ctx.get("image"),
            "difficulty": meta.get("difficulty"),
            "noise_level": meta.get("noise_level"),
            "failure_phase": meta.get("failure_phase"),
            # Text targets
            "evidence_text": build_evidence_text(row),
            "diagnosis_text": rem.get("diagnosis") or "",
            "fix_plan_text": _join_list(rem.get("fix_plan")),
            "actions_text": flatten_actions_structured(rem.get("actions_structured")),
            "verification_text": _join_list(rem.get("verification")),
            "rollback_text": _join_list(rem.get("rollback")),
        }
        records.append(record)

    return pd.DataFrame.from_records(records)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Transform synthetic incident JSONL into agent-specific datasets.")
    p.add_argument(
        "--input",
        type=str,
        required=True,
        help="Path to raw incident JSONL (e.g., data/synthetic_source.jsonl)",
    )
    p.add_argument(
        "--outdir",
        type=str,
        required=True,
        help="Output directory for transformed Parquet files (will be created).",
    )
    return p.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = _parse_args(argv)
    input_path = Path(args.input)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"[load] reading incidents from {input_path}")
    df_raw = load_incidents(input_path)
    print(f"[load] {len(df_raw):,} incidents loaded")

    print("[agent1] building structured RCA dataset...")
    agent1_df = build_agent1_structured(df_raw)
    agent1_path = outdir / "agent1_structured.parquet"
    write_parquet(agent1_df, agent1_path)
    print(f"[agent1] wrote {len(agent1_df):,} rows -> {agent1_path}")

    print("[agent2] building semantic evidence dataset...")
    agent2_df = build_agent2_evidence(df_raw)
    agent2_path = outdir / "agent2_evidence.parquet"
    write_parquet(agent2_df, agent2_path)
    print(f"[agent2] wrote {len(agent2_df):,} rows -> {agent2_path}")

    print("[done]")


if __name__ == "__main__":
    main()

