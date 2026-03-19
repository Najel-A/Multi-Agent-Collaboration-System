"""Remediation Agent — generates fix plan, commands, verification, and rollback."""

from typing import Any

from agents.base_agent import AgentResult, BaseAgent

# Category → (fix_steps, verification, rollback) templates
REMEDIATION_TEMPLATES: dict[str, dict[str, Any]] = {
    "MissingSecret": {
        "fix_plan": [
            "Create the missing Secret in the namespace (prefer GitOps).",
            "Sync/reconcile GitOps or apply directly and restart workload.",
            "Verify pod becomes Ready and errors stop.",
        ],
        "verification": ["Pod Ready; no secret-not-found warnings."],
        "rollback": ["Revert Secret creation and sync/reconcile."],
    },
    "MissingConfigMapKey": {
        "fix_plan": [
            "Add the missing key to the ConfigMap (prefer GitOps).",
            "Sync/reconcile or apply directly and restart workload.",
            "Verify pod becomes Ready.",
        ],
        "verification": ["Pod Ready; no configmap-key warnings."],
        "rollback": ["Revert ConfigMap changes and sync/reconcile."],
    },
    "ImagePullBadTag": {
        "fix_plan": [
            "Update image tag to a valid, existing tag.",
            "Apply change and wait for new pod to pull the correct image.",
            "Verify pod becomes Ready.",
        ],
        "verification": ["Pod Ready; image pulled successfully."],
        "rollback": ["Revert image tag change."],
    },
    "ImagePullAuth": {
        "fix_plan": [
            "Create imagePullSecret with valid registry credentials.",
            "Reference it from the ServiceAccount or Pod spec.",
            "Apply and verify image pull succeeds.",
        ],
        "verification": ["Pod Ready; no unauthorized errors."],
        "rollback": ["Remove imagePullSecret reference if incorrect."],
    },
    "FailedScheduling_Taint": {
        "fix_plan": [
            "Add toleration for the untolerated taint, or adjust taint/nodeSelector strategy.",
            "Apply change and verify pod schedules.",
        ],
        "verification": ["Pod scheduled and Running."],
        "rollback": ["Revert toleration change."],
    },
    "FailedScheduling_Memory": {
        "fix_plan": [
            "Reduce memory request to a realistic value, or add larger nodes.",
            "Apply change and verify scheduling succeeds.",
        ],
        "verification": ["Pod scheduled and Running."],
        "rollback": ["Restore previous resource values."],
    },
    "FailedScheduling_CPU": {
        "fix_plan": [
            "Reduce CPU request to a realistic value, or add nodes with more CPU.",
            "Apply change and verify scheduling succeeds.",
        ],
        "verification": ["Pod scheduled and Running."],
        "rollback": ["Restore previous resource values."],
    },
    "FailedScheduling_NodeSelector": {
        "fix_plan": [
            "Remove or correct nodeSelector/affinity to match real node labels.",
            "Apply change and verify pod schedules.",
        ],
        "verification": ["Pod scheduled and Running."],
        "rollback": ["Revert nodeSelector change."],
    },
    "MissingStorageClass": {
        "fix_plan": [
            "Ensure a valid StorageClass exists and PVC references it.",
            "Apply changes and verify PVC becomes Bound.",
            "Confirm pod becomes Running/Ready.",
        ],
        "verification": ["PVC Bound; pod Running/Ready."],
        "rollback": ["Revert StorageClass/PVC changes."],
    },
    "FailedMount": {
        "fix_plan": [
            "Create the missing PVC (prefer GitOps) or update volume claimName.",
            "Apply change and verify mount succeeds.",
        ],
        "verification": ["No FailedMount; pod Running/Ready."],
        "rollback": ["Revert PVC creation/volume changes."],
    },
    "OOMKilled": {
        "fix_plan": [
            "Increase memory limit (and adjust request if needed).",
            "Apply change and rollout restart.",
            "Verify restarts stop increasing and pod becomes Ready.",
        ],
        "verification": ["Pod Ready; restarts stop increasing."],
        "rollback": ["Restore prior limit and investigate memory usage."],
    },
    "ReadinessProbeFailure": {
        "fix_plan": [
            "Confirm readiness probe path/port/initialDelay match app behavior.",
            "Fix dependency connectivity or adjust probe timeouts.",
            "Apply changes and verify pod becomes Ready.",
        ],
        "verification": ["READY becomes 1/1; endpoints include pod."],
        "rollback": ["Revert probe changes."],
    },
    "LivenessProbeFailure": {
        "fix_plan": [
            "Review liveness probe path/port/timeout; increase initialDelaySeconds.",
            "Investigate app slowness (CPU throttling, GC pauses, dependency latency).",
            "Apply probe tuning and verify restarts stop.",
        ],
        "verification": ["Restarts stop increasing; pod Ready."],
        "rollback": ["Revert liveness probe change."],
    },
    "RBAC_Forbidden": {
        "fix_plan": [
            "Create/update Role and RoleBinding granting least-privilege access.",
            "Apply via GitOps (preferred) and restart workload.",
            "Verify Forbidden error disappears.",
        ],
        "verification": ["Forbidden error gone; pod stabilizes."],
        "rollback": ["Revert RBAC changes."],
    },
    "DNSFailure": {
        "fix_plan": [
            "Confirm the Service name/namespace is correct and service exists.",
            "Check CoreDNS health and cluster DNS configuration.",
            "Fix service reference or restore DNS; restart workload.",
        ],
        "verification": ["Hostname resolves; app connects; pod Ready."],
        "rollback": ["Revert DNS/service config change."],
    },
    "ConnectionRefused": {
        "fix_plan": [
            "Check if the dependency Service has endpoints and backing pods are Ready.",
            "Verify the port is correct and no NetworkPolicy blocks traffic.",
            "Restore the dependency or fix service config; restart workload.",
        ],
        "verification": ["Connection succeeds; pod Ready."],
        "rollback": ["Revert service/port changes."],
    },
    "QuotaExceeded": {
        "fix_plan": [
            "Identify pods to clean up (completed Jobs, old replicas) or increase quota.",
            "Apply cleanup/quota change, then retry rollout.",
        ],
        "verification": ["New pods created; rollout proceeds."],
        "rollback": ["Revert quota increase; prefer cleanup/rightsizing."],
    },
    "GitOpsSyncFailed": {
        "fix_plan": [
            "Fix the YAML/template error in the GitOps repo (validate manifests locally).",
            "Re-run GitOps sync/reconcile and verify it becomes Healthy.",
            "Confirm workload pods are created/updated.",
        ],
        "verification": ["GitOps Healthy/Ready; workload resources applied."],
        "rollback": ["Revert the commit that introduced invalid YAML; re-sync."],
    },
}

DEFAULT_REMEDIATION = {
    "fix_plan": [
        "Investigate the incident signals (logs, events, describe).",
        "Identify and fix the root cause.",
        "Verify workload recovery.",
    ],
    "verification": ["Pod becomes Ready."],
    "rollback": ["Revert changes if they worsen the situation."],
}


class RemediationAgent(BaseAgent):
    """Generates an actionable remediation plan based on the diagnosis.

    Produces: ordered fix steps, kubectl/gitops commands,
    verification checks, and rollback procedures.
    """

    def __init__(self, model: Any = None):
        super().__init__(name="remediation", model=model)

    def run(self, incident: dict[str, Any]) -> AgentResult:
        diag = incident.get("_diagnostic_result", {})
        category = diag.get("category", "Unknown")
        diagnosis = diag.get("diagnosis", "")
        ctx = incident.get("context", {})

        if self.model is not None:
            prompt = self._build_prompt(incident)
            raw = self._call_model(prompt)
            findings = self._parse_model_output(raw)
        else:
            findings = self._plan_rule_based(category, diagnosis, ctx)

        return AgentResult(
            agent_name=self.name,
            status="success",
            findings=findings,
            confidence=0.85 if self.model else 0.7,
            raw_output=str(findings.get("fix_plan", [])),
        )

    def _build_prompt(self, incident: dict[str, Any]) -> str:
        diag = incident.get("_diagnostic_result", {})
        ctx = incident.get("context", {})
        return (
            "You are a Kubernetes SRE. Generate a safe, ordered remediation plan.\n\n"
            f"CLUSTER: {ctx.get('cluster_id', '')}\n"
            f"NAMESPACE: {ctx.get('namespace', '')}\n"
            f"WORKLOAD: {ctx.get('workload_kind', '')}/{ctx.get('workload_name', '')}\n"
            f"GITOPS: {ctx.get('gitops_enabled', False)} ({ctx.get('gitops_tool', 'none')})\n\n"
            f"DIAGNOSIS: {diag.get('diagnosis', '')}\n"
            f"CATEGORY: {diag.get('category', '')}\n\n"
            "Respond in format:\n"
            "FIX_PLAN:\n1. <step>\n2. <step>\n...\n"
            "COMMANDS:\n- <command>\n...\n"
            "VERIFICATION:\n- <check>\n...\n"
            "ROLLBACK:\n- <step>\n..."
        )

    @staticmethod
    def _plan_rule_based(
        category: str, diagnosis: str, ctx: dict[str, Any]
    ) -> dict[str, Any]:
        template = REMEDIATION_TEMPLATES.get(category, DEFAULT_REMEDIATION)
        ns = ctx.get("namespace", "<namespace>")
        workload = ctx.get("workload_name", "<workload>")
        gitops_enabled = ctx.get("gitops_enabled", False)
        gitops_tool = ctx.get("gitops_tool", "")

        # Build commands based on category and gitops config
        commands = RemediationAgent._generate_commands(
            category, ns, workload, gitops_enabled, gitops_tool
        )

        return {
            "diagnosis": diagnosis,
            "fix_plan": template["fix_plan"],
            "commands": commands,
            "verification": template["verification"],
            "rollback": template["rollback"],
            "gitops_aware": gitops_enabled,
        }

    @staticmethod
    def _generate_commands(
        category: str, ns: str, workload: str,
        gitops_enabled: bool, gitops_tool: str
    ) -> list[str]:
        commands: list[str] = []

        if gitops_enabled:
            if gitops_tool == "flux":
                commands.append(f"# Edit manifests in GitOps repo, then:")
                commands.append(f"flux reconcile kustomization <name> --with-source")
            else:
                commands.append(f"# Edit manifests in GitOps repo, then:")
                commands.append(f"argocd app sync <app-name>")
                commands.append(f"argocd app wait <app-name> --health")
        else:
            if category in ("MissingSecret",):
                commands.append(f"kubectl -n {ns} create secret generic <name> --from-literal=<KEY>=<VALUE>")
                commands.append(f"kubectl -n {ns} rollout restart deploy/{workload}")
            elif category in ("ImagePullBadTag",):
                commands.append(f"kubectl -n {ns} set image deploy/{workload} <container>=<image>:<valid-tag>")
            elif category in ("OOMKilled", "FailedScheduling_Memory", "FailedScheduling_CPU"):
                commands.append(f"kubectl -n {ns} patch deploy/{workload} --type merge -p '<resources patch>'")
            else:
                commands.append(f"kubectl -n {ns} edit deploy/{workload}")

        commands.append(f"kubectl -n {ns} get pods")
        commands.append(f"kubectl -n {ns} rollout status deploy/{workload}")
        return commands

    @staticmethod
    def _parse_model_output(raw: str) -> dict[str, Any]:
        fix_plan: list[str] = []
        commands: list[str] = []
        verification: list[str] = []
        rollback: list[str] = []
        current: list[str] | None = None

        for line in raw.splitlines():
            stripped = line.strip()
            upper = stripped.upper()
            if upper.startswith("FIX_PLAN:"):
                current = fix_plan
            elif upper.startswith("COMMANDS:"):
                current = commands
            elif upper.startswith("VERIFICATION:"):
                current = verification
            elif upper.startswith("ROLLBACK:"):
                current = rollback
            elif current is not None and stripped:
                # Strip leading "1. " or "- "
                cleaned = stripped.lstrip("0123456789.-) ").strip()
                if cleaned:
                    current.append(cleaned)

        return {
            "fix_plan": fix_plan,
            "commands": commands,
            "verification": verification,
            "rollback": rollback,
        }
