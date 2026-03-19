"""State Analyst Agent — extracts findings from kubectl describe, events, and gitops status."""

import re
from typing import Any

from agents.base_agent import AgentResult, BaseAgent

# Event patterns to detect from kubectl events + describe output
STATE_PATTERNS: list[tuple[str, str, str]] = [
    # (regex, finding_key, description)
    (r"secret\s+\"([^\"]+)\"\s+not found",                         "missing_secret",       "Secret not found"),
    (r"configmap\s+\"([^\"]+)\"\s+does not contain key\s+\"([^\"]+)\"", "missing_configmap_key", "ConfigMap missing key"),
    (r"Failed to pull image.*manifest unknown",                     "image_manifest_unknown", "Image tag does not exist"),
    (r"unauthorized:\s*authentication required",                    "registry_auth_failed",  "Registry auth failed"),
    (r"untolerated taint\s*\{([^}]+)\}",                           "untolerated_taint",     "Untolerated node taint"),
    (r"Insufficient\s+(memory|cpu)",                                "insufficient_resource", "Insufficient node resources"),
    (r"node\(s\) didn't match.*node affinity/selector",             "nodeselector_mismatch", "Node selector mismatch"),
    (r"persistentvolumeclaim\s+\"([^\"]+)\"\s+not found",           "pvc_not_found",         "PVC not found"),
    (r"unbound immediate PersistentVolumeClaims",                   "pvc_unbound",           "PVC unbound"),
    (r"exceeded quota.*pods",                                       "quota_exceeded",        "Pod quota exceeded"),
    (r"Readiness probe failed.*statuscode:\s*(\d+)",                "readiness_probe_fail",  "Readiness probe failed"),
    (r"Liveness probe failed",                                      "liveness_probe_fail",   "Liveness probe failed"),
    (r"Container.*OOMKilled",                                       "oomkilled",             "Container OOMKilled"),
    (r"Back-off restarting failed container",                       "crashloop_backoff",     "CrashLoop back-off"),
    (r"GitOps sync failed",                                         "gitops_sync_failed",    "GitOps sync failure"),
]


class StateAnalystAgent(BaseAgent):
    """Analyzes Kubernetes object state (describe, events, gitops status).

    Extracts resource issues, config mismatches, scheduling blocks,
    and RBAC errors from structured kubectl output.
    """

    def __init__(self, model: Any = None):
        super().__init__(name="state_analyst", model=model)

    def run(self, incident: dict[str, Any]) -> AgentResult:
        obs = incident.get("observations", {})
        describe = obs.get("kubectl_describe_pod") or ""
        events = obs.get("kubectl_get_events") or ""
        gitops = obs.get("gitops_status") or ""
        metrics = obs.get("metrics_snapshot") or {}

        if self.model is not None:
            prompt = self._build_prompt(incident)
            raw = self._call_model(prompt)
            findings = self._parse_model_output(raw)
        else:
            findings = self._analyze_rule_based(describe, events, gitops, metrics)

        return AgentResult(
            agent_name=self.name,
            status="success",
            findings=findings,
            confidence=0.85 if self.model else 0.7,
            raw_output=(events[:300] + "\n" + describe[:300]),
        )

    def _build_prompt(self, incident: dict[str, Any]) -> str:
        obs = incident.get("observations", {})
        ctx = incident.get("context", {})
        return (
            "Analyze this Kubernetes cluster state. Identify:\n"
            "1. Resource or scheduling issues\n"
            "2. Configuration problems (secrets, configmaps, images)\n"
            "3. RBAC or permission errors\n"
            "4. GitOps sync status issues\n\n"
            f"WORKLOAD: {ctx.get('workload_kind', '')}/{ctx.get('workload_name', '')}\n"
            f"NAMESPACE: {ctx.get('namespace', '')}\n\n"
            f"=== kubectl describe pod ===\n{obs.get('kubectl_describe_pod', '')}\n"
            f"=== events ===\n{obs.get('kubectl_get_events', '')}\n"
            f"=== gitops status ===\n{obs.get('gitops_status', '')}\n\n"
            "Respond in format:\n"
            "ISSUES: [list of issues found]\n"
            "RESOURCE_STATE: <pod state and restart count>\n"
            "SUMMARY: <one sentence summary>"
        )

    @staticmethod
    def _analyze_rule_based(
        describe: str, events: str, gitops: str, metrics: dict
    ) -> dict[str, Any]:
        combined = describe + "\n" + events + "\n" + gitops
        issues: list[dict[str, str]] = []
        matched_keys: set[str] = set()

        for pattern, key, description in STATE_PATTERNS:
            match = re.search(pattern, combined, re.IGNORECASE)
            if match and key not in matched_keys:
                matched_keys.add(key)
                issues.append({
                    "type": key,
                    "description": description,
                    "detail": match.group(0).strip(),
                })

        # Extract pod state from describe
        resource_state = StateAnalystAgent._extract_pod_state(describe)

        # Check metrics
        restarts = metrics.get("restarts", 0)
        oom_killed = metrics.get("oom_killed", False)
        if oom_killed and "oomkilled" not in matched_keys:
            issues.append({
                "type": "oomkilled",
                "description": "Container OOMKilled (from metrics)",
                "detail": f"oom_killed=True, restarts={restarts}",
            })

        # GitOps status
        gitops_healthy = True
        if gitops:
            if "Ready=False" in gitops or "Health=Degraded" in gitops or "Synced=False" in gitops:
                gitops_healthy = False

        # Summary
        if issues:
            summary = issues[0]["description"] + ": " + issues[0]["detail"]
        else:
            summary = "No clear state issues detected."

        return {
            "issues": issues,
            "resource_state": resource_state,
            "restarts": restarts,
            "oom_killed": oom_killed,
            "gitops_healthy": gitops_healthy,
            "summary": summary,
        }

    @staticmethod
    def _extract_pod_state(describe: str) -> dict[str, str]:
        state: dict[str, str] = {}
        # Extract Reason from State: Waiting / Reason:
        reason_match = re.search(r"State:\s*Waiting\s*\n\s*Reason:\s*(\S+)", describe)
        if reason_match:
            state["waiting_reason"] = reason_match.group(1)

        restart_match = re.search(r"Restart Count:\s*(\d+)", describe)
        if restart_match:
            state["restart_count"] = restart_match.group(1)

        exit_match = re.search(r"Exit Code:\s*(\d+)", describe)
        if exit_match:
            state["exit_code"] = exit_match.group(1)

        return state

    @staticmethod
    def _parse_model_output(raw: str) -> dict[str, Any]:
        issues: list[str] = []
        resource_state = ""
        summary = ""
        for line in raw.splitlines():
            upper = line.strip().upper()
            if upper.startswith("ISSUES:"):
                issues = [i.strip() for i in line.split(":", 1)[1].split(",")]
            elif upper.startswith("RESOURCE_STATE:"):
                resource_state = line.split(":", 1)[1].strip()
            elif upper.startswith("SUMMARY:"):
                summary = line.split(":", 1)[1].strip()
        return {"issues": issues, "resource_state": resource_state, "summary": summary}
