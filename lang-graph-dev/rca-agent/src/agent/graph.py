from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict

import httpx
from langgraph.graph import StateGraph
from dotenv import load_dotenv

load_dotenv()

LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-change-this-master-key")

MODEL_NAME = os.getenv("MODEL_NAME", "qwen-tool")
READONLY_MCP_SERVER = os.getenv("READONLY_MCP_SERVER", "k8s_mcp_readonly")
DEFAULT_NAMESPACE = os.getenv("DEFAULT_NAMESPACE", "test-app")


@dataclass
class State:
    alert: dict = field(default_factory=dict)
    namespace: str = ""
    pod: str = ""
    message: str = ""
    evidence: dict = field(default_factory=dict)

    pod_verified: bool = False
    matched_pod: str = ""

    diagnosis: str = ""


def extract_alert_fields(state: State) -> Dict[str, Any]:
    labels = state.alert.get("labels", {})

    namespace = labels.get("namespace", DEFAULT_NAMESPACE)
    pod = labels.get("pod", "")

    return {
        "namespace": namespace,
        "pod": pod,
        "message": f"Received alert for namespace={namespace}, pod={pod}",
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


async def collect_pods(state: State) -> Dict[str, Any]:
    result = await call_mcp_tool(
        tool_name=f"{READONLY_MCP_SERVER}-pods_list_in_namespace",
        arguments={
            "namespace": state.namespace,
        },
    )

    return {
        "evidence": {
            "pods": result,
        }
    }


def get_pod_list_text(state: State) -> str:
    return (
        state.evidence
        .get("pods", {})
        .get("result", {})
        .get("content", [{}])[0]
        .get("text", "")
    )


def validate_alert_pod(state: State) -> Dict[str, Any]:
    pod_text = get_pod_list_text(state)

    if state.pod and state.pod in pod_text:
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

Important validation:
- Alert namespace: {state.namespace}
- Alert pod: {state.pod}
- Pod verified in live Kubernetes evidence: {state.pod_verified}
- Matched pod: {state.matched_pod}

Rules:
- Do not invent cluster state.
- Only use the Kubernetes evidence provided below.
- If pod_verified is false, do NOT say the alert pod is affected.
- If pod_verified is false, explain that the alert pod was not found and analyze the actual pods shown in the evidence.
- Mention likely stale/deleted/replaced alert if the alert pod is missing.

Alert JSON:
{json.dumps(state.alert, indent=2)}

Kubernetes evidence:
{json.dumps(state.evidence, indent=2)}

Return:
1. whether the alert pod exists
2. actual unhealthy pods found
3. probable root cause based only on evidence
4. next evidence to collect
5. recommended fix plan
6. whether auto-fix is safe or needs human approval
"""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a Kubernetes RCA and remediation agent. "
                    "You must not hallucinate resources. "
                    "Trust live MCP/Kubernetes evidence over alert labels."
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
    .add_node("extract_alert_fields", extract_alert_fields)
    .add_node("collect_pods", collect_pods)
    .add_node("validate_alert_pod", validate_alert_pod)
    .add_node("diagnose_with_llm", diagnose_with_llm)
    .add_edge("__start__", "extract_alert_fields")
    .add_edge("extract_alert_fields", "collect_pods")
    .add_edge("collect_pods", "validate_alert_pod")
    .add_edge("validate_alert_pod", "diagnose_with_llm")
    .compile(name="K8s RCA Graph")
)


if __name__ == "__main__":
    import asyncio

    test_alert = {
        "labels": {
            "alertname": "KubePodCrashLooping",
            "namespace": DEFAULT_NAMESPACE,
            "pod": "broken-app-123",
        },
        "annotations": {
            "summary": "Pod is crash looping",
        },
    }

    result = asyncio.run(
        graph.ainvoke(
            {
                "alert": test_alert,
            }
        )
    )

    print(json.dumps(result, indent=2))