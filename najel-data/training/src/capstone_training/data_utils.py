from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

from .schema_utils import SchemaReport, SchemaSpec, normalize_columns, report_schema


@dataclass(frozen=True)
class LoadSummary:
    files: list[str]
    rows: int
    schema_reports: dict[str, SchemaReport]


def iter_jsonl_records(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSONL at {path} line {i}: {e}") from e


def load_jsonl(path: Path) -> pd.DataFrame:
    records = list(iter_jsonl_records(path))
    return pd.DataFrame.from_records(records)


def load_jsonl_folder(
    folder: Path,
    *,
    glob: str = "*.jsonl",
    schema: Optional[SchemaSpec] = None,
    allow_extra_columns: bool = True,
) -> tuple[pd.DataFrame, LoadSummary]:
    """
    Load multiple JSONL files and (optionally) validate a shared schema.

    - Missing required columns are reported and raise an error.
    - Extra columns are allowed by default (reported for visibility).
    """
    folder = Path(folder)
    files = sorted(folder.glob(glob))
    if not files:
        raise FileNotFoundError(f"No JSONL files matched {glob} in {folder}")

    dfs: list[pd.DataFrame] = []
    schema_reports: dict[str, SchemaReport] = {}

    for fp in files:
        df = load_jsonl(fp)
        if schema is not None:
            rep = report_schema(df.columns, schema)
            schema_reports[str(fp)] = rep
            if rep.required_missing:
                raise ValueError(
                    f"{fp} missing required columns: {sorted(rep.required_missing)}"
                )
            if (not allow_extra_columns) and rep.unexpected_extra:
                raise ValueError(
                    f"{fp} has unexpected extra columns: {sorted(rep.unexpected_extra)}"
                )
            df = normalize_columns(df, schema, fill_missing_optional=True)
        df["__source_file"] = str(fp)
        dfs.append(df)

    merged = pd.concat(dfs, ignore_index=True, sort=False)
    summary = LoadSummary(
        files=[str(p) for p in files],
        rows=len(merged),
        schema_reports=schema_reports,
    )
    return merged, summary


def summarize_incident_df(
    df: pd.DataFrame,
    *,
    scenario_col: str = "scenario_id",
    text_col: str = "evidence_text",
    sample_n: int = 3,
) -> dict[str, Any]:
    """
    Return a notebook-friendly summary dict (you can pretty-print it).
    """
    summary: dict[str, Any] = {}
    summary["rows"] = int(len(df))
    summary["columns"] = list(df.columns)

    if scenario_col in df.columns:
        vc = df[scenario_col].fillna("∅").astype(str).value_counts()
        summary["scenario_distribution"] = vc.to_dict()

    # Null summary (top 25 columns)
    null_counts = df.isna().sum().sort_values(ascending=False)
    summary["null_counts_top"] = null_counts.head(25).to_dict()

    # Basic text stats
    if text_col in df.columns:
        lengths = df[text_col].fillna("").astype(str).map(len)
        summary["text_len"] = {
            "min": int(lengths.min()),
            "p50": int(lengths.quantile(0.5)),
            "p90": int(lengths.quantile(0.9)),
            "max": int(lengths.max()),
        }

    # Samples
    samples = df.head(sample_n).to_dict(orient="records")
    summary["samples"] = samples
    return summary

