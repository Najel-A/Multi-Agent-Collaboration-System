"""Diagnostic Agent — synthesizes findings from triage, log, and state agents into a root cause."""

from typing import Any

from agents.base_agent import AgentResult, BaseAgent

# Category → diagnosis templates for rule-based fallback
DIAGNOSIS_TEMPLATES: dict[str, str] = {
    "MissingSecret":            "Pod cannot start because a required Secret is missing: {detail}",
    "MissingConfigMapKey":      "Pod cannot start because a ConfigMap key is missing: {detail}",
    "ImagePullBadTag":          "Image pull fails because the image tag does not exist (manifest unknown).",
    "ImagePullAuth":            "Image pull fails due to missing/invalid registry credentials (unauthorized).",
    "FailedScheduling_Taint":   "Pod cannot schedule due to untolerated node taint: {detail}",
    "FailedScheduling_Memory":  "Pod cannot schedule because its memory request exceeds available node capacity.",
    "FailedScheduling_CPU":     "Pod cannot schedule because its CPU request exceeds available node capacity.",
    "FailedScheduling_NodeSelector": "Pod cannot schedule because nodeSelector/affinity does not match any nodes.",
    "MissingStorageClass":      "Pod is Pending because PVC is unbound (missing/invalid StorageClass).",
    "FailedMount":              "Pod cannot mount volume because the PVC does not exist: {detail}",
    "OOMKilled":                "Container is repeatedly OOMKilled because memory limit is too low.",
    "ReadinessProbeFailure":    "Pod is running but NotReady due to failing readiness probe.",
    "LivenessProbeFailure":     "Container restarts due to failing liveness probe (timeout/incorrect path).",
    "RBAC_Forbidden":           "Workload fails due to RBAC permission error (Forbidden) for its ServiceAccount.",
    "DNSFailure":               "Workload fails due to DNS resolution failure for a cluster service.",
    "ConnectionRefused":        "App cannot connect to dependency service (connection refused).",
    "QuotaExceeded":            "Workload cannot create pods because namespace ResourceQuota is exceeded.",
    "GitOpsSyncFailed":         "GitOps is failing to sync/apply due to manifest rendering/YAML error.",
    "CrashLoopBackOff":         "Container crashes on startup: {detail}",
    "ConfigError":              "Container config error: {detail}",
    "Pending":                  "Pod is stuck in Pending state: {detail}",
}


class DiagnosticAgent(BaseAgent):
    """Synthesizes triage category + log findings + state findings
    into a single root cause diagnosis.

    In rule-based mode, selects a diagnosis template based on the
    triage category and fills it with detail from other agents.
    In model-based mode, sends all findings to the LLM.
    """

    def __init__(self, model: Any = None):
        super().__init__(name="diagnostic", model=model)

    def run(self, incident: dict[str, Any]) -> AgentResult:
        triage = incident.get("_triage_result", {})
        log_findings = incident.get("_log_result", {})
        state_findings = incident.get("_state_result", {})

        category = triage.get("category", "Unknown")

        if self.model is not None:
            prompt = self._build_prompt(incident)
            raw = self._call_model(prompt)
            diagnosis = self._parse_model_output(raw)
        else:
            diagnosis = self._diagnose_rule_based(category, log_findings, state_findings)

        return AgentResult(
            agent_name=self.name,
            status="success",
            findings={
                "category": category,
                "diagnosis": diagnosis,
                "contributing_signals": {
                    "log_summary": log_findings.get("summary", ""),
                    "state_summary": state_findings.get("summary", ""),
                },
            },
            confidence=0.9 if self.model else 0.75,
            raw_output=diagnosis,
        )

    def _build_prompt(self, incident: dict[str, Any]) -> str:
        triage = incident.get("_triage_result", {})
        log_findings = incident.get("_log_result", {})
        state_findings = incident.get("_state_result", {})
        ctx = incident.get("context", {})

        return (
            "You are a Kubernetes diagnostic expert. Based on the following "
            "findings from specialist agents, provide a single root cause diagnosis.\n\n"
            f"CLUSTER: {ctx.get('cluster_id', '')}\n"
            f"NAMESPACE: {ctx.get('namespace', '')}\n"
            f"WORKLOAD: {ctx.get('workload_kind', '')}/{ctx.get('workload_name', '')}\n\n"
            f"TRIAGE: category={triage.get('category', '')}, "
            f"severity={triage.get('severity', '')}\n\n"
            f"LOG ANALYSIS:\n"
            f"  Errors: {log_findings.get('errors', [])}\n"
            f"  Summary: {log_findings.get('summary', '')}\n\n"
            f"STATE ANALYSIS:\n"
            f"  Issues: {state_findings.get('issues', [])}\n"
            f"  Summary: {state_findings.get('summary', '')}\n\n"
            "Respond with a single sentence diagnosis:\n"
            "DIAGNOSIS: <root cause>"
        )

    @staticmethod
    def _diagnose_rule_based(
        category: str,
        log_findings: dict[str, Any],
        state_findings: dict[str, Any],
    ) -> str:
        # Collect detail from the most informative source
        detail_parts: list[str] = []

        # From log errors
        log_errors = log_findings.get("errors", [])
        if isinstance(log_errors, list) and log_errors:
            if isinstance(log_errors[0], dict):
                detail_parts.append(log_errors[0].get("detail", ""))
            else:
                detail_parts.append(str(log_errors[0]))

        # From state issues
        state_issues = state_findings.get("issues", [])
        if isinstance(state_issues, list) and state_issues:
            if isinstance(state_issues[0], dict):
                detail_parts.append(state_issues[0].get("detail", ""))
            else:
                detail_parts.append(str(state_issues[0]))

        detail = "; ".join(d for d in detail_parts if d) or "see signals for details"

        template = DIAGNOSIS_TEMPLATES.get(category, "Incident detected: {detail}")
        return template.format(detail=detail)

    @staticmethod
    def _parse_model_output(raw: str) -> str:
        for line in raw.splitlines():
            if line.strip().upper().startswith("DIAGNOSIS:"):
                return line.split(":", 1)[1].strip()
        return raw.strip()
