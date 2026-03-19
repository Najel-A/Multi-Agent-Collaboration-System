"""Triage Agent — classifies incident category and severity from pod status."""

import re
from typing import Any

from agents.base_agent import AgentResult, BaseAgent

# Maps pod status keywords → (category, severity)
STATUS_RULES: dict[str, tuple[str, str]] = {
    "CrashLoopBackOff":            ("CrashLoopBackOff",   "high"),
    "ImagePullBackOff":            ("ImagePullBackOff",   "high"),
    "ErrImagePull":                ("ImagePullBackOff",   "high"),
    "CreateContainerConfigError":  ("ConfigError",        "high"),
    "OOMKilled":                   ("OOMKilled",          "critical"),
    "Pending":                     ("Pending",            "medium"),
    "Error":                       ("CrashLoopBackOff",   "high"),
}

# Refine category with event/describe signals
EVENT_REFINEMENTS: list[tuple[str, str]] = [
    (r"FailedScheduling.*taint",            "FailedScheduling_Taint"),
    (r"FailedScheduling.*Insufficient\s+memory", "FailedScheduling_Memory"),
    (r"FailedScheduling.*Insufficient\s+cpu",    "FailedScheduling_CPU"),
    (r"FailedScheduling.*node affinity|selector", "FailedScheduling_NodeSelector"),
    (r"FailedMount|persistentvolumeclaim",  "FailedMount"),
    (r"exceeded quota",                     "QuotaExceeded"),
    (r"Forbidden",                          "RBAC_Forbidden"),
    (r"SyncFailed|GitOps sync failed",      "GitOpsSyncFailed"),
    (r"Liveness probe failed",              "LivenessProbeFailure"),
    (r"Readiness probe failed",             "ReadinessProbeFailure"),
    (r"unauthorized.*authentication",       "ImagePullAuth"),
    (r"manifest unknown",                   "ImagePullBadTag"),
    (r"secret.*not found",                  "MissingSecret"),
    (r"configmap.*does not contain key",    "MissingConfigMapKey"),
    (r"no such host|dns",                   "DNSFailure"),
    (r"connection refused",                 "ConnectionRefused"),
    (r"OOMKilled",                          "OOMKilled"),
    (r"StorageClass",                       "MissingStorageClass"),
]


class TriageAgent(BaseAgent):
    """Classifies the incident into a category and severity level.

    Works in two modes:
    1. Rule-based (no model) — pattern matching on kubectl output
    2. Model-based — fine-tuned classifier predicts category
    """

    def __init__(self, model: Any = None):
        super().__init__(name="triage", model=model)

    def run(self, incident: dict[str, Any]) -> AgentResult:
        obs = incident.get("observations", {})
        get_pods = obs.get("kubectl_get_pods", "")
        events = obs.get("kubectl_get_events", "")
        describe = obs.get("kubectl_describe_pod", "")

        if self.model is not None:
            prompt = self._build_prompt(incident)
            raw = self._call_model(prompt)
            category, severity = self._parse_model_output(raw)
        else:
            category, severity = self._classify_rule_based(get_pods, events, describe)

        return AgentResult(
            agent_name=self.name,
            status="success",
            findings={
                "category": category,
                "severity": severity,
            },
            confidence=0.9 if self.model else 0.75,
            raw_output=f"category={category} severity={severity}",
        )

    def _build_prompt(self, incident: dict[str, Any]) -> str:
        obs = incident.get("observations", {})
        return (
            "Classify this Kubernetes incident into exactly one category "
            "and severity (low/medium/high/critical).\n\n"
            f"=== kubectl get pods ===\n{obs.get('kubectl_get_pods', '')}\n"
            f"=== events ===\n{obs.get('kubectl_get_events', '')}\n\n"
            "Respond in format:\nCATEGORY: <category>\nSEVERITY: <severity>"
        )

    def _classify_rule_based(
        self, get_pods: str, events: str, describe: str
    ) -> tuple[str, str]:
        """Two-pass classification: status keywords → event refinement."""
        # Pass 1: pod status
        category = "Unknown"
        severity = "medium"
        for keyword, (cat, sev) in STATUS_RULES.items():
            if keyword in get_pods:
                category = cat
                severity = sev
                break

        # Pass 2: refine with events + describe
        combined = events + " " + describe
        for pattern, refined_cat in EVENT_REFINEMENTS:
            if re.search(pattern, combined, re.IGNORECASE):
                category = refined_cat
                break

        return category, severity

    @staticmethod
    def _parse_model_output(raw: str) -> tuple[str, str]:
        category = "Unknown"
        severity = "medium"
        for line in raw.splitlines():
            line_upper = line.strip().upper()
            if line_upper.startswith("CATEGORY:"):
                category = line.split(":", 1)[1].strip()
            elif line_upper.startswith("SEVERITY:"):
                severity = line.split(":", 1)[1].strip().lower()
        return category, severity
