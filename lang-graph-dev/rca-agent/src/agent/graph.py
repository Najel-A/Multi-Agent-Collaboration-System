from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict

import httpx
from dotenv import load_dotenv
from langgraph.graph import StateGraph


load_dotenv()


LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-change-this-master-key")

MODEL_NAME = os.getenv("MODEL_NAME", "qwen-tool")
READONLY_MCP_SERVER = os.getenv("READONLY_MCP_SERVER", "k8s_mcp_readonly")
DEFAULT_NAMESPACE = os.getenv("DEFAULT_NAMESPACE", "test-app")


@dataclass
class State:
    # Input
    alert: dict = field(default_factory=dict)
    raw_alert_text: str = ""

    # Parsed alert fields
    alertname: str = ""
    namespace: str = ""
    deployment: str = ""
    pod: str = ""
    container: str = ""
    reason: str = ""
    severity: str = ""
    summary: str = ""
    description: str = ""
    starts_at: str = ""
    received_at: str = ""
    generator_url: str = ""
    runbook_url: str = ""

    # Runtime state
    evidence: dict = field(default_factory=dict)
    pod_verified: bool = False
    matched_pod: str = ""
    diagnosis: str = ""
    message: str = ""


def parse_alert(state: State) -> Dict[str, Any]:
    alert = state.alert or {}
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})

    if labels:
        return {
            "alertname": labels.get("alertname", ""),
            "namespace": labels.get("namespace", DEFAULT_NAMESPACE),
            "deployment": labels.get("deployment", ""),
            "pod": labels.get("pod", ""),
            "container": labels.get("container", ""),
            "reason": labels.get("reason", ""),
            "severity": labels.get("severity", ""),
            "summary": annotations.get("summary", ""),
            "description": annotations.get("description", ""),
            "starts_at": alert.get("startsAt", ""),
            "received_at": alert.get("receivedAt", ""),
            "generator_url": alert.get("generatorURL", ""),
            "runbook_url": annotations.get("runbook_url", ""),
            "message": "Parsed structured alert payload.",
        }

    text = state.raw_alert_text or ""

    def extract(prefix: str) -> str:
        for line in text.splitlines():
            if line.strip().startswith(prefix):
                return line.split(":", 1)[1].strip()
        return ""

    return {
        "alertname": extract("Alert Name"),
        "namespace": extract("Namespace") or DEFAULT_NAMESPACE,
        "deployment": extract("Deployment").replace("—", "").strip(),
        "pod": extract("Pod"),
        "container": extract("Container"),
        "reason": extract("Reason"),
        "severity": extract("Severity"),
        "summary": extract("Summary"),
        "description": extract("Description"),
        "starts_at": extract("Starts At"),
        "received_at": extract("Received At"),
        "generator_url": extract("Generator URL"),
        "runbook_url": extract("Runbook URL"),
        "message": "Parsed raw alert text.",
    }


async def call_mcp_tool(tool_name: str, arguments: dict) -> dict:
    url = f"{LITELLM_BASE_URL}/{READONLY_MCP_SERVER}/mcp"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "x-litellm-api-key": f"Bearer {LITELLM_API_KEY}",
    }

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(url, headers=headers, json=payload)

    response.raise_for_status()

    for line in response.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line.replace("data: ", "", 1))

    return {"raw": response.text}


def mcp_is_error(result: dict) -> bool:
    return bool(result.get("result", {}).get("isError", False) or result.get("error"))


async def collect_evidence(state: State) -> Dict[str, Any]:
    evidence: dict[str, Any] = {}

    evidence["pods"] = await call_mcp_tool(
        tool_name=f"{READONLY_MCP_SERVER}-pods_list_in_namespace",
        arguments={"namespace": state.namespace},
    )

    evidence["events"] = await call_mcp_tool(
        tool_name=f"{READONLY_MCP_SERVER}-events_list",
        arguments={"namespace": state.namespace},
    )

    if state.pod:
        pod_result = await call_mcp_tool(
            tool_name=f"{READONLY_MCP_SERVER}-pods_get",
            arguments={
                "namespace": state.namespace,
                "name": state.pod,
            },
        )
        evidence["target_pod"] = pod_result

        if not mcp_is_error(pod_result):
            log_args = {
                "namespace": state.namespace,
                "name": state.pod,
                "tail": 100,
            }

            if state.container:
                log_args["container"] = state.container

            evidence["target_pod_logs"] = await call_mcp_tool(
                tool_name=f"{READONLY_MCP_SERVER}-pods_log",
                arguments=log_args,
            )

    return {"evidence": evidence}


def validate_alert_pod(state: State) -> Dict[str, Any]:
    target_pod = state.evidence.get("target_pod", {})

    if state.pod and not mcp_is_error(target_pod):
        return {
            "pod_verified": True,
            "matched_pod": state.pod,
            "message": f"Alert pod '{state.pod}' was found in namespace '{state.namespace}'.",
        }

    return {
        "pod_verified": False,
        "matched_pod": "",
        "message": (
            f"Alert pod '{state.pod}' was NOT found in namespace "
            f"'{state.namespace}'. Diagnosis must use actual MCP evidence only."
        ),
    }


async def diagnose_with_llm(state: State) -> Dict[str, Any]:
    url = f"{LITELLM_BASE_URL}/v1/chat/completions"

    prompt = f"""
You are a Kubernetes RCA agent.

Parsed alert:
- Alert name: {state.alertname}
- Namespace: {state.namespace}
- Deployment: {state.deployment}
- Pod: {state.pod}
- Container: {state.container}
- Reason: {state.reason}
- Severity: {state.severity}
- Summary: {state.summary}
- Description: {state.description}
- Starts at: {state.starts_at}
- Received at: {state.received_at}
- Runbook URL: {state.runbook_url}

Validation:
- Pod verified in live Kubernetes evidence: {state.pod_verified}
- Matched pod: {state.matched_pod}

Rules:
- Do not invent cluster state.
- Trust live MCP/Kubernetes evidence over alert labels.
- If pod_verified is false, do not claim the alert pod is currently present.
- Use logs/events/pod status as primary evidence.
- Do not recommend destructive actions unless clearly justified.
- Prefer human approval before write/fix actions.

Live Kubernetes evidence:
{json.dumps(state.evidence, indent=2)}

Return:
1. alert validity
2. affected workload/pod/container
3. probable root cause
4. exact evidence used
5. recommended next evidence if needed
6. recommended fix plan
7. whether auto-fix is safe or human approval is required
"""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a Kubernetes RCA and remediation agent. "
                    "Only reason from provided alert data and live Kubernetes MCP evidence."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.1,
    }

    headers = {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(url, headers=headers, json=payload)

    response.raise_for_status()
    data = response.json()

    return {
        "diagnosis": data["choices"][0]["message"]["content"],
    }


graph = (
    StateGraph(State)
    .add_node("parse_alert", parse_alert)
    .add_node("collect_evidence", collect_evidence)
    .add_node("validate_alert_pod", validate_alert_pod)
    .add_node("diagnose_with_llm", diagnose_with_llm)
    .add_edge("__start__", "parse_alert")
    .add_edge("parse_alert", "collect_evidence")
    .add_edge("collect_evidence", "validate_alert_pod")
    .add_edge("validate_alert_pod", "diagnose_with_llm")
    .compile(name="K8s RCA Graph")
)


if __name__ == "__main__":
    import asyncio

    test_alert_text = """
Alert Name: KubePodCrashLooping
Namespace: test-app
Deployment: —
Pod: broken-log-generator-8554bbc567-fjr2p
Container: broken-log-generator
Reason: CrashLoopBackOff
Severity: warning
Summary: Pod is crash looping.
Description: Pod test-app/broken-log-generator-8554bbc567-fjr2p (broken-log-generator) is in waiting state (reason: "CrashLoopBackOff") on cluster .
Starts At: 2026-05-08T10:10:07.701Z
Received At: 2026-05-09T03:05:37.906Z
Runbook URL: https://runbooks.prometheus-operator.dev/runbooks/kubernetes/kubepodcrashlooping
"""

    result = asyncio.run(
        graph.ainvoke(
            {
                "raw_alert_text": test_alert_text,
            }
        )
    )

    print(json.dumps(result, indent=2))