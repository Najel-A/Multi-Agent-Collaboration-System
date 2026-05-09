from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal

import httpx
from dotenv import load_dotenv
from langgraph.graph import StateGraph


load_dotenv()

LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000").rstrip("/")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-change-this-master-key")

MODEL_NAME = os.getenv("MODEL_NAME", "rca-qwen")
WRITE_MCP_SERVER = os.getenv("WRITE_MCP_SERVER", "k8s_mcp_fix_agent")
DEFAULT_NAMESPACE = os.getenv("DEFAULT_NAMESPACE", "test-app")

LLM_READ_TIMEOUT_SECONDS = float(os.getenv("LLM_READ_TIMEOUT_SECONDS", "600"))
MCP_TIMEOUT_SECONDS = float(os.getenv("MCP_TIMEOUT_SECONDS", "120"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "500"))


ALLOWED_TOOLS = {
    f"{WRITE_MCP_SERVER}-pods_delete",
    f"{WRITE_MCP_SERVER}-resources_scale",
    f"{WRITE_MCP_SERVER}-resources_create_or_update",
}


@dataclass
class State:
    alert: dict = field(default_factory=dict)

    diagnosis_1: str = ""
    diagnosis_2: str = ""
    chosen_diagnosis: str = ""

    namespace: str = DEFAULT_NAMESPACE
    pod: str = ""
    deployment: str = ""
    container: str = ""

    approval: bool = False
    selected_action: Dict[str, Any] = field(default_factory=dict)

    proposed_actions: List[Dict[str, Any]] = field(default_factory=list)
    execution_result: Dict[str, Any] = field(default_factory=dict)

    status: str = ""
    message: str = ""


def parse_input(state: State) -> Dict[str, Any]:
    labels = (state.alert or {}).get("labels", {})

    return {
        "namespace": state.namespace or labels.get("namespace") or DEFAULT_NAMESPACE,
        "pod": state.pod or labels.get("pod", ""),
        "deployment": state.deployment or labels.get("deployment", ""),
        "container": state.container or labels.get("container", ""),
    }


def route_after_parse(state: State) -> Literal["execute_requested", "plan_requested"]:
    if state.approval:
        return "execute_requested"
    return "plan_requested"


async def generate_fix_plan(state: State) -> Dict[str, Any]:
    url = f"{LITELLM_BASE_URL}/v1/chat/completions"

    prompt = f"""
You are a Kubernetes remediation planner.

You must NOT execute anything.
You only generate proposed actions for a human to approve.

Allowed write tools:
- {WRITE_MCP_SERVER}-pods_delete
- {WRITE_MCP_SERVER}-resources_scale
- {WRITE_MCP_SERVER}-resources_create_or_update

Blocked tools:
- pods_exec
- pods_run
- resources_delete

Rules:
- Return JSON only.
- No markdown.
- No chain-of-thought.
- Do not invent missing objects.
- Only propose actions in namespace: {DEFAULT_NAMESPACE}
- Human approval is always required.
- Prefer pods_delete for CrashLoopBackOff if a Deployment/ReplicaSet will recreate the pod.
- Do not delete PVCs, namespaces, nodes, secrets, or deployments.

Context:
Namespace: {state.namespace}
Pod: {state.pod}
Deployment: {state.deployment}
Container: {state.container}

Diagnosis 1:
{state.diagnosis_1}

Diagnosis 2:
{state.diagnosis_2}

Chosen diagnosis:
{state.chosen_diagnosis}

Return exactly this JSON shape:

{{
  "proposed_actions": [
    {{
      "action_id": "delete_pod",
      "title": "Delete unhealthy pod so Kubernetes recreates it",
      "risk": "low",
      "requires_human_approval": true,
      "tool_name": "{WRITE_MCP_SERVER}-pods_delete",
      "arguments": {{
        "namespace": "{state.namespace}",
        "name": "{state.pod}"
      }},
      "reason": "Pod is unhealthy and should be recreated by its controller."
    }}
  ]
}}
"""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a Kubernetes remediation planner. "
                    "Return JSON only. Do not execute actions."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": LLM_MAX_TOKENS,
    }

    headers = {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "Content-Type": "application/json",
    }

    timeout = httpx.Timeout(
        connect=10.0,
        read=LLM_READ_TIMEOUT_SECONDS,
        write=30.0,
        pool=30.0,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)

        response.raise_for_status()
        data = response.json()

        content = data["choices"][0]["message"]["content"].strip()
        parsed = json.loads(content)

        return {
            "proposed_actions": parsed.get("proposed_actions", []),
            "status": "WAITING_FOR_HUMAN_APPROVAL",
            "message": "Fix plan generated. Human approval required before execution.",
        }

    except Exception as e:
        return {
            "proposed_actions": [],
            "status": "PLAN_FAILED",
            "message": f"Failed to generate fix plan: {str(e)}",
        }


def validate_selected_action(state: State) -> tuple[bool, str]:
    action = state.selected_action or {}

    tool_name = action.get("tool_name", "")
    arguments = action.get("arguments", {})

    if not action:
        return False, "approval=true but selected_action was empty."

    if tool_name not in ALLOWED_TOOLS:
        return False, f"Tool '{tool_name}' is not allowed."

    namespace = arguments.get("namespace", "")

    if namespace != DEFAULT_NAMESPACE:
        return False, f"Executor can only write in namespace '{DEFAULT_NAMESPACE}'."

    if tool_name == f"{WRITE_MCP_SERVER}-pods_delete":
        if not arguments.get("name"):
            return False, "pods_delete requires arguments.name."

    if tool_name == f"{WRITE_MCP_SERVER}-resources_scale":
        if not arguments.get("name"):
            return False, "resources_scale requires arguments.name."
        if "replicas" not in arguments:
            return False, "resources_scale requires arguments.replicas."

    if tool_name == f"{WRITE_MCP_SERVER}-resources_create_or_update":
        manifest = arguments.get("resource") or arguments.get("manifest")
        if not manifest:
            return False, "resources_create_or_update requires arguments.resource or arguments.manifest."

    return True, "Action validated."


async def call_write_mcp_tool(tool_name: str, arguments: dict) -> dict:
    url = f"{LITELLM_BASE_URL}/{WRITE_MCP_SERVER}/mcp"

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

    async with httpx.AsyncClient(timeout=MCP_TIMEOUT_SECONDS) as client:
        response = await client.post(url, headers=headers, json=payload)

    response.raise_for_status()

    for line in response.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line.replace("data: ", "", 1))

    return {"raw": response.text}


async def execute_approved_action(state: State) -> Dict[str, Any]:
    valid, reason = validate_selected_action(state)

    if not valid:
        return {
            "status": "EXECUTION_BLOCKED",
            "message": reason,
            "execution_result": {},
        }

    action = state.selected_action
    tool_name = action["tool_name"]
    arguments = action.get("arguments", {})

    try:
        result = await call_write_mcp_tool(tool_name, arguments)

        return {
            "status": "EXECUTED",
            "message": "Approved write MCP action executed.",
            "execution_result": result,
        }

    except Exception as e:
        return {
            "status": "EXECUTION_FAILED",
            "message": f"Write MCP execution failed: {str(e)}",
            "execution_result": {},
        }


graph = (
    StateGraph(State)
    .add_node("parse_input", parse_input)
    .add_node("generate_fix_plan", generate_fix_plan)
    .add_node("execute_approved_action", execute_approved_action)
    .add_edge("__start__", "parse_input")
    .add_conditional_edges(
        "parse_input",
        route_after_parse,
        {
            "plan_requested": "generate_fix_plan",
            "execute_requested": "execute_approved_action",
        },
    )
    .compile(name="K8s Executor Graph")
)