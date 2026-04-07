"""
SFT templates for mk's three scenarios — same style as Rgb29's TEMPLATES dict.

Merge into the main TEMPLATES map used by the training / transform pipeline, e.g.:

    from scripts.real_k8s_mk.mk_scenario_templates import MK_SFT_TEMPLATES
    TEMPLATES = {**TEMPLATES, **MK_SFT_TEMPLATES}

Placeholders should be filled from the transform output (namespace, workload_name,
pod_name, service_account_name, event_*, claim_name, etc.) when available.
Use .format_map or a safe formatter with a defaultdict for missing keys.
"""

from __future__ import annotations

MK_SFT_TEMPLATES = {
    "dns_resolution_failure": {
        "fix_plan_text": (
            "For workload {workload_name} in namespace {namespace}, fix DNS resolution for the "
            "hostname or Kubernetes Service name the application cannot resolve (confirm the failing "
            "name from pod logs and events on pod {pod_name}). Verify whether the target Service exists "
            "in the intended namespace, that the FQDN follows cluster DNS conventions "
            "(e.g. <service>.<namespace>.svc.cluster.local), and that CoreDNS (or the cluster DNS "
            "provider) is healthy. Correct application configuration, environment variables, ConfigMaps, "
            "or Helm values that reference the wrong name. Create or restore missing Services if they "
            "are supposed to exist. After applying the fix, restart or roll out workload {workload_name} "
            "and verify that lookup errors (e.g. NXDOMAIN or no such host) no longer appear."
        ),
        "rollback_text": (
            "For workload {workload_name} in namespace {namespace}, restore the previous hostname, "
            "Service reference, or configuration that existed before remediation if the change was "
            "incorrect. If a Service or DNS-related manifest was added only for testing, remove it after "
            "confirming it is safe. Re-verify that workload {workload_name} matches the prior intended "
            "naming and dependency layout."
        ),
        "verification_text": (
            "Verify that workload {workload_name} in namespace {namespace} resolves the intended "
            "dependency from within the pod (Pod {pod_name} or replacement pod), that application "
            "startup succeeds, and that logs no longer show DNS resolution failures for the corrected name."
        ),
    },
    "service_connection_refused": {
        "fix_plan_text": (
            "For workload {workload_name} in namespace {namespace}, fix the TCP connection that returns "
            "connection refused (see pod {pod_name} logs). Confirm the target address resolves to the "
            "correct Service or Pod IP, that the port matches a listening process, and that the backing "
            "Service has Ready Endpoints. Start or repair the dependency workload if it is down; correct "
            "the client host or port if misconfigured. Check NetworkPolicies only if traffic should be "
            "allowed but is blocked. After changes, restart or roll out workload {workload_name} and "
            "verify that connection refused errors no longer appear in logs."
        ),
        "rollback_text": (
            "For workload {workload_name} in namespace {namespace}, restore the previous client "
            "configuration (host, port, or dependency reference) if the remediation introduced incorrect "
            "routing. If a Service, port, or deployment change was made on the downstream workload, "
            "revert only when safe and approved. Confirm configuration matches the prior known state."
        ),
        "verification_text": (
            "Verify that workload {workload_name} in namespace {namespace} can open a successful TCP "
            "connection to the intended dependency, that Pod {pod_name} (or its replacement) reaches a "
            "stable Ready state, and that logs show no recurring connection refused errors."
        ),
    },
    "quota_exceeded_pods": {
        "fix_plan_text": (
            "For workload {workload_name} in namespace {namespace}, resolve namespace ResourceQuota so "
            "new Pods can be created (inspect FailedCreate or quota messages in events — reason may "
            "appear as {event_reason} with detail {event_message}). Compare quota limits to current Pod "
            "count and usage with `kubectl describe resourcequota` and `kubectl get pods -n {namespace}`. "
            "Free capacity by removing completed or unnecessary Pods, right-size replica counts, or "
            "request an approved increase to limits. After quota or usage is corrected, re-evaluate "
            "workload {workload_name} until ReplicaSet or Deployment can create Pods without forbidden "
            "quota errors."
        ),
        "rollback_text": (
            "For workload {workload_name} in namespace {namespace}, restore the previous ResourceQuota "
            "or workload replica settings if the change overstretched shared namespace capacity or was "
            "made in error. Remove only Pods that are confirmed safe to delete. Ensure quota limits "
            "match the agreed policy after rollback."
        ),
        "verification_text": (
            "Verify that namespace {namespace} can create new Pods for workload {workload_name}, that "
            "events no longer show exceeded quota or FailedCreate tied to pod limits, and that the "
            "rollout completes successfully."
        ),
    },
}

__all__ = ["MK_SFT_TEMPLATES", "MERGE_INSTRUCTIONS"]

MERGE_INSTRUCTIONS = """
Example merge:

    TEMPLATES = {
        **EXISTING_TEMPLATES,
        **MK_SFT_TEMPLATES,
    }

When formatting, supply defaults for missing keys (e.g. workload_name from describe,
namespace from transform output, pod_name, event_reason, event_message).
"""
