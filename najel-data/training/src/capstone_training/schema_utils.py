from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Set


@dataclass(frozen=True)
class SchemaSpec:
    """
    A flexible schema spec:
    - required columns must exist
    - optional columns may exist
    - extra columns are allowed (reported, not fatal by default)
    """

    required: Set[str]
    optional: Set[str]

    @classmethod
    def k8s_incident_v1(cls) -> "SchemaSpec":
        required = {
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
        }

        # Keep optional open-ended; teammates may add columns (metrics, tags, ids, etc.)
        optional = {
            "id",
            "cluster_id",
            "error_message",
            "restart_count",
            "event_type",
            "event_reason",
            "event_message",
            "symptom_family",
            "root_cause_family",
            "difficulty",
            "noise_level",
            "failure_phase",
            "oom_killed",
            "missing_secret",
            "missing_key",
        }
        return cls(required=required, optional=optional)


@dataclass(frozen=True)
class SchemaReport:
    required_missing: Set[str]
    unexpected_extra: Set[str]
    present: Set[str]

    @property
    def ok(self) -> bool:
        return len(self.required_missing) == 0


def report_schema(columns: Iterable[str], spec: SchemaSpec) -> SchemaReport:
    present = set(columns)
    required_missing = set(spec.required) - present
    allowed = set(spec.required) | set(spec.optional)
    unexpected_extra = present - allowed
    return SchemaReport(
        required_missing=required_missing,
        unexpected_extra=unexpected_extra,
        present=present,
    )


def assert_schema(columns: Sequence[str], spec: SchemaSpec, *, allow_extra: bool = True) -> None:
    rep = report_schema(columns, spec)
    if rep.required_missing:
        raise ValueError(f"Missing required columns: {sorted(rep.required_missing)}")
    if (not allow_extra) and rep.unexpected_extra:
        raise ValueError(f"Unexpected extra columns: {sorted(rep.unexpected_extra)}")


def normalize_columns(df, spec: SchemaSpec, *, fill_missing_optional: bool = True):
    """
    Ensure required columns exist; optionally add missing optional columns as None.
    """
    for col in spec.required:
        if col not in df.columns:
            df[col] = None
    if fill_missing_optional:
        for col in spec.optional:
            if col not in df.columns:
                df[col] = None
    return df

