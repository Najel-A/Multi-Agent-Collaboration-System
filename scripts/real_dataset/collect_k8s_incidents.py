#!/usr/bin/env python3
"""
Collect real Kubernetes incident rows for a fixed set of failure scenarios.

For each scenario this script:
  1. Creates a unique namespace (random suffix).
  2. Applies YAML (Deployment or StatefulSet) that reproduces the fault.
  3. Waits for the pod to show the expected failure mode.
  4. Runs: kubectl get pods, describe pod, get events, logs (best-effort).
  5. Emits one JSON object per run (JSONL) aligned with agent2-style fields.

Requirements:
  - kubectl configured against a cluster (Kind, minikube, EKS, etc.)
  - Network access to pull public images (busybox, python, etc.)

Usage:
  python collect_k8s_incidents.py
  python collect_k8s_incidents.py --scenarios crashloop_bad_args oomkilled_limit_too_low --workload-kind StatefulSet

Default output: scripts/real_dataset/dataset/real_incidents.jsonl (folder is created automatically).
Each captured row is written and flushed immediately so stopping mid-run (Ctrl+C) still keeps prior captures.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import random
import shutil
import string
import subprocess
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "2"

# All generated JSONL/Parquet should live here (see dataset/README.md).
_DATASET_DIR = Path(__file__).resolve().parent / "dataset"
DEFAULT_OUTPUT_FILE = _DATASET_DIR / "real_incidents.jsonl"


def kubectl(args: List[str], input_text: Optional[str] = None) -> Tuple[int, str, str]:
    """Run kubectl; return (returncode, stdout, stderr)."""
    kw: Dict[str, Any] = {}
    if input_text is not None:
        # With text=True, subprocess expects str for stdin (not bytes).
        kw["input"] = input_text
    p = subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        **kw,
    )
    return p.returncode, p.stdout or "", p.stderr or ""


def require_kubectl() -> None:
    if shutil.which("kubectl") is None:
        sys.exit("kubectl not found in PATH. Install kubectl and configure a context.")


def random_namespace(prefix: str = "rca-real") -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{prefix}-{suffix}"


def kubectl_apply_yaml(yaml_text: str) -> None:
    rc, out, err = kubectl(["apply", "-f", "-"], input_text=yaml_text)
    if rc != 0:
        raise RuntimeError(f"kubectl apply failed: {err or out}")


def kubectl_delete_namespace(ns: str) -> None:
    subprocess.run(
        ["kubectl", "delete", "namespace", ns, "--wait=false"],
        capture_output=True,
        text=True,
    )


def get_first_pod_name(ns: str) -> Optional[str]:
    rc, out, _ = kubectl(["get", "pods", "-n", ns, "-o", "jsonpath={.items[0].metadata.name}"])
    if rc != 0 or not out.strip():
        return None
    return out.strip()


def wait_for_pod_name(ns: str, timeout_s: int = 120) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        name = get_first_pod_name(ns)
        if name:
            return name
        time.sleep(2)
    raise TimeoutError(f"No pod appeared in namespace {ns} within {timeout_s}s")


def get_pod_json(ns: str, pod: str) -> Dict[str, Any]:
    rc, out, err = kubectl(["get", "pod", pod, "-n", ns, "-o", "json"])
    if rc != 0:
        raise RuntimeError(f"kubectl get pod -o json failed: {err}")
    return json.loads(out)


def extract_pod_fields(pod_obj: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    """Returns created_at, phase, waiting_reason, container_name, image."""
    meta = pod_obj.get("metadata") or {}
    created = meta.get("creationTimestamp") or ""
    status = pod_obj.get("status") or {}
    phase = status.get("phase") or ""

    container_name = ""
    image = ""
    waiting_reason = ""

    cs_list = (status.get("containerStatuses") or []) + (status.get("initContainerStatuses") or [])
    if cs_list:
        c0 = cs_list[0]
        container_name = c0.get("name") or ""
        image = c0.get("image") or ""
        st = c0.get("state") or {}
        last_st = c0.get("lastState") or {}
        if "waiting" in st:
            waiting_reason = (st["waiting"] or {}).get("reason") or ""
        elif "terminated" in st:
            waiting_reason = (st["terminated"] or {}).get("reason") or ""
        elif "terminated" in last_st:
            waiting_reason = (last_st["terminated"] or {}).get("reason") or ""

    return created, phase, waiting_reason, container_name, image


def capture_outputs(ns: str, pod: str) -> Tuple[str, str, str, str]:
    """get pods wide, describe pod, events, logs."""
    _, get_pods, _ = kubectl(["get", "pods", "-n", ns, "-o", "wide"])
    _, describe, _ = kubectl(["describe", "pod", pod, "-n", ns])
    _, events, _ = kubectl(
        ["get", "events", "-n", ns, "--sort-by=.lastTimestamp"]
    )
    rc, logs, err = kubectl(["logs", pod, "-n", ns, "--tail=200"])
    if rc != 0 or not logs.strip():
        rc_prev, logs_prev, err_prev = kubectl(
            ["logs", pod, "-n", ns, "--previous", "--tail=200"]
        )
        if rc_prev == 0 and logs_prev.strip():
            logs = logs_prev
        else:
            msg = (err or err_prev or "").strip() or "no logs"
            logs = f"(logs unavailable: {msg})"
    return get_pods, describe, events, logs


def build_evidence_text(
    namespace: str,
    workload_kind: str,
    workload_name: str,
    container_name: str,
    image: str,
    get_pods: str,
    describe: str,
    events: str,
    logs: str,
) -> str:
    """Same section layout as transform_incidents.build_evidence_text."""
    header_lines = [
        f"namespace: {namespace}",
        f"workload: {workload_name}",
        f"workload_kind: {workload_kind}",
        f"container: {container_name}",
        f"image: {image}",
    ]
    header = "\n".join(header_lines)
    parts = [
        header,
        "=== kubectl get pods ===",
        get_pods.rstrip(),
        "=== kubectl describe pod ===",
        describe.rstrip(),
        "=== kubectl get events ===",
        events.rstrip(),
        "=== container logs ===",
        logs.rstrip(),
    ]
    return "\n".join(parts).strip()


# --- Scenario-specific YAML and remediation text ---------------------------------


def manifest_crashloop_bad_args(ns: str, workload_kind: str, workload_name: str) -> str:
    if workload_kind == "StatefulSet":
        return f"""apiVersion: v1
kind: Service
metadata:
  name: {workload_name}-headless
  namespace: {ns}
spec:
  clusterIP: None
  selector:
    app: crashloop-app
  ports:
  - port: 80
    targetPort: 80
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {workload_name}
  namespace: {ns}
spec:
  serviceName: {workload_name}-headless
  replicas: 1
  selector:
    matchLabels:
      app: crashloop-app
  template:
    metadata:
      labels:
        app: crashloop-app
    spec:
      containers:
      - name: main
        image: busybox:1.36
        command: ["/bin/sh", "-c"]
        args: ["exit 42"]
"""
    return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {workload_name}
  namespace: {ns}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: crashloop-app
  template:
    metadata:
      labels:
        app: crashloop-app
    spec:
      containers:
      - name: main
        image: busybox:1.36
        command: ["/bin/sh", "-c"]
        args: ["exit 42"]
"""


def manifest_oom_killed(ns: str, workload_kind: str, workload_name: str) -> str:
    # Interpreter starts within limit; allocation exceeds limit -> OOMKilled
    if workload_kind == "StatefulSet":
        return f"""apiVersion: v1
kind: Service
metadata:
  name: {workload_name}-headless
  namespace: {ns}
spec:
  clusterIP: None
  selector:
    app: oom-app
  ports:
  - port: 80
    targetPort: 80
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {workload_name}
  namespace: {ns}
spec:
  serviceName: {workload_name}-headless
  replicas: 1
  selector:
    matchLabels:
      app: oom-app
  template:
    metadata:
      labels:
        app: oom-app
    spec:
      containers:
      - name: main
        image: python:3.11-alpine
        command: ["python", "-c"]
        args:
          - "import time; x=b'x' * (300 * 1024 * 1024); time.sleep(3600)"
        resources:
          limits:
            memory: "128Mi"
            cpu: "500m"
          requests:
            memory: "64Mi"
            cpu: "100m"
"""
    return f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {workload_name}
  namespace: {ns}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: oom-app
  template:
    metadata:
      labels:
        app: oom-app
    spec:
      containers:
      - name: main
        image: python:3.11-alpine
        command: ["python", "-c"]
        args:
          - "import time; x=b'x' * (300 * 1024 * 1024); time.sleep(3600)"
        resources:
          limits:
            memory: "128Mi"
            cpu: "500m"
          requests:
            memory: "64Mi"
            cpu: "100m"
"""


def dockerconfig_invalid_dockerhub() -> str:
    """Invalid credentials for Docker Hub -> 401 on pull with imagePullSecrets."""
    auth = base64.b64encode(b"invalid:invalid").decode()
    cfg = {
        "auths": {
            "https://index.docker.io/v1/": {
                "username": "invalid",
                "password": "invalid",
                "auth": auth,
            }
        }
    }
    return json.dumps(cfg)


def manifest_imagepull_registry_auth(
    ns: str, workload_kind: str, workload_name: str, image_ref: str
) -> str:
    """
    Use an image that REQUIRES registry credentials (private repo). Invalid
    dockerconfigjson forces ErrImagePull / 401-style failures. Public images
    often still pull anonymously — do not use busybox for training-quality data.
    """
    secret_name = "bad-registry-creds"
    docker_cfg = dockerconfig_invalid_dockerhub()
    if workload_kind == "StatefulSet":
        return f"""apiVersion: v1
kind: Secret
metadata:
  name: {secret_name}
  namespace: {ns}
type: kubernetes.io/dockerconfigjson
stringData:
  .dockerconfigjson: '{docker_cfg}'
---
apiVersion: v1
kind: Service
metadata:
  name: {workload_name}-headless
  namespace: {ns}
spec:
  clusterIP: None
  selector:
    app: pullfail-app
  ports:
  - port: 80
    targetPort: 80
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {workload_name}
  namespace: {ns}
spec:
  serviceName: {workload_name}-headless
  replicas: 1
  selector:
    matchLabels:
      app: pullfail-app
  template:
    metadata:
      labels:
        app: pullfail-app
    spec:
      imagePullSecrets:
      - name: {secret_name}
      containers:
      - name: main
        image: {image_ref}
        imagePullPolicy: Always
"""
    return f"""apiVersion: v1
kind: Secret
metadata:
  name: {secret_name}
  namespace: {ns}
type: kubernetes.io/dockerconfigjson
stringData:
  .dockerconfigjson: '{docker_cfg}'
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {workload_name}
  namespace: {ns}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: pullfail-app
  template:
    metadata:
      labels:
        app: pullfail-app
    spec:
      imagePullSecrets:
      - name: {secret_name}
      containers:
      - name: main
        image: {image_ref}
        imagePullPolicy: Always
"""


REMEDIATION_BASE: Dict[str, Dict[str, str]] = {
    "crashloop_bad_args": {
        "diagnosis_text": (
            "The container command exits with a non-zero status immediately on start. "
            "In this workload the args run `exit 42`, so Kubernetes restarts the container "
            "repeatedly and the pod enters CrashLoopBackOff."
        ),
        "fix_plan_text": (
            "1. Inspect the Deployment/StatefulSet container command and args.\n"
            "2. Fix the startup command or fix application configuration so the process stays running.\n"
            "3. Apply the updated manifest and roll out.\n"
            "4. Verify pod reaches Running and readiness succeeds."
        ),
        "verification_text": (
            "Pod status is Running; `kubectl logs` shows stable output; restart count stops increasing."
        ),
    },
    "oomkilled_limit_too_low": {
        "diagnosis_text": (
            "The container is terminated with reason OOMKilled because its working set exceeds "
            "the memory limit configured in resources.limits.memory."
        ),
        "fix_plan_text": (
            "1. Confirm OOMKilled in `kubectl describe pod`.\n"
            "2. Reduce memory usage (fix leaks, lower cache) or raise the limit appropriately.\n"
            "3. Re-deploy and watch memory metrics.\n"
            "4. Add/adjust readiness if the app needs warm-up."
        ),
        "verification_text": (
            "Pod stays Running under load; no OOMKilled events; node memory pressure is acceptable."
        ),
    },
    "imagepull_registry_auth": {
        "diagnosis_text": (
            "Image pull fails because registry authentication is wrong or the referenced "
            "imagePullSecret contains invalid credentials. Events typically show Failed with "
            "401 Unauthorized or similar from the registry."
        ),
        "fix_plan_text": (
            "1. Verify the correct registry, repository, and tag.\n"
            "2. Create or fix a docker-registry Secret (dockerconfigjson) with valid credentials.\n"
            "3. Reference the secret in podTemplate.imagePullSecrets.\n"
            "4. Re-check events until the image pulls successfully."
        ),
        "verification_text": (
            "Pod schedules; `kubectl describe pod` shows image pulled successfully; container starts."
        ),
    },
}


def remediation_for_scenario(
    scenario_id: str, workload_kind: str, workload_name: str
) -> Dict[str, str]:
    """kubectl resource names aligned with Deployment vs StatefulSet."""
    wn = workload_name
    base = REMEDIATION_BASE[scenario_id]
    out = dict(base)

    if workload_kind == "StatefulSet":
        edit = f"[kubectl] kubectl -n <namespace> edit statefulset {wn}"
        roll_status = f"[kubectl] kubectl -n <namespace> rollout status statefulset/{wn}"
        roll_undo = f"[kubectl] kubectl -n <namespace> rollout undo statefulset/{wn}"
        set_res = (
            f"[kubectl] kubectl -n <namespace> set resources statefulset {wn} "
            "--limits=memory=512Mi"
        )
        patch_pull = (
            f"[kubectl] kubectl -n <namespace> patch statefulset {wn} -p "
            '\'{"spec":{"template":{"spec":{"imagePullSecrets":[{"name":"regcred"}]}}}}\''
        )
        roll_restart = f"[kubectl] kubectl -n <namespace> rollout restart statefulset/{wn}"
    else:
        edit = f"[kubectl] kubectl -n <namespace> edit deployment {wn}"
        roll_status = f"[kubectl] kubectl -n <namespace> rollout status deployment/{wn}"
        roll_undo = f"[kubectl] kubectl -n <namespace> rollout undo deployment/{wn}"
        set_res = (
            f"[kubectl] kubectl -n <namespace> set resources deployment {wn} "
            "--limits=memory=512Mi"
        )
        patch_pull = (
            f"[kubectl] kubectl -n <namespace> patch deployment {wn} -p "
            '\'{"spec":{"template":{"spec":{"imagePullSecrets":[{"name":"regcred"}]}}}}\''
        )
        roll_restart = f"[kubectl] kubectl -n <namespace> rollout restart deployment/{wn}"

    if scenario_id == "crashloop_bad_args":
        out["actions_text"] = (
            "[kubectl] kubectl -n <namespace> get deploy,sts\n"
            f"{edit}\n"
            f"{roll_status}\n"
            "[kubectl] kubectl -n <namespace> logs <pod_name> --previous"
        )
        out["rollback_text"] = roll_undo
    elif scenario_id == "oomkilled_limit_too_low":
        out["actions_text"] = (
            "[kubectl] kubectl -n <namespace> describe pod <pod_name>\n"
            f"{set_res}\n"
            f"{roll_status}"
        )
        out["rollback_text"] = roll_undo
    elif scenario_id == "imagepull_registry_auth":
        out["actions_text"] = (
            "[kubectl] kubectl -n <namespace> create secret docker-registry regcred "
            "--docker-server=https://index.docker.io/v1/ --docker-username=<user> "
            "--docker-password=<token> --docker-email=<email>\n"
            f"{patch_pull}\n"
            f"{roll_restart}"
        )
        out["rollback_text"] = (
            f"{roll_undo}\n"
            "[kubectl] kubectl -n <namespace> delete secret bad-registry-creds"
        )
    else:
        raise KeyError(scenario_id)

    return out


SCENARIO_IDS = (
    "crashloop_bad_args",
    "oomkilled_limit_too_low",
    "imagepull_registry_auth",
)

# Coarse grouping for multi-class experiments / mixing datasets
FAILURE_CLASS: Dict[str, str] = {
    "crashloop_bad_args": "application_crash",
    "oomkilled_limit_too_low": "resource_exhaustion",
    "imagepull_registry_auth": "registry_auth",
}


def manifest_for_scenario(
    scenario_id: str,
    ns: str,
    workload_kind: str,
    workload_name: str,
    private_pull_image: str,
) -> str:
    if scenario_id == "crashloop_bad_args":
        return manifest_crashloop_bad_args(ns, workload_kind, workload_name)
    if scenario_id == "oomkilled_limit_too_low":
        return manifest_oom_killed(ns, workload_kind, workload_name)
    if scenario_id == "imagepull_registry_auth":
        return manifest_imagepull_registry_auth(
            ns, workload_kind, workload_name, private_pull_image
        )
    raise KeyError(scenario_id)


def validate_capture(scenario_id: str, describe: str, pod_obj: Dict[str, Any]) -> None:
    """Refuse to emit rows that do not match the intended failure (training safety)."""
    compact = describe.lower().replace(" ", "").replace("\n", "")
    d = describe.lower()
    if scenario_id == "crashloop_bad_args":
        if "crashloopbackoff" not in compact:
            raise RuntimeError(
                "Validation failed: expected CrashLoopBackOff in `kubectl describe` output. "
                "Skip this sample; do not add to the training set."
            )
    elif scenario_id == "oomkilled_limit_too_low":
        if "oomkilled" not in d:
            raise RuntimeError(
                "Validation failed: expected OOMKilled in describe. "
                "Tune limits/alloc or cluster memory; skip this sample."
            )
    elif scenario_id == "imagepull_registry_auth":
        phase = (pod_obj.get("status") or {}).get("phase") or ""
        cs_list = (pod_obj.get("status") or {}).get("containerStatuses") or []
        ready = bool(cs_list and cs_list[0].get("ready"))
        if phase == "Running" and ready:
            raise RuntimeError(
                "Validation failed: pod Running and container ready — pull succeeded. "
                "Use a private image with --private-pull-image (or RCA_PRIVATE_PULL_IMAGE)."
            )
        # Auth / pull-denied: strict strings first; then Docker Desktop / containerd
        # often omit "401" but still say "denied", "login", "incorrect", etc.
        auth_in_describe = (
            "401",
            "403",
            "Unauthorized",
            "unauthorized",
            "insufficient_scope",
            "InsufficientScope",
            "pull access denied",
            "Pull access denied",
        )
        auth_in_lower = (
            "pull access denied",
            "insufficient_scope",
            "access denied",
            "incorrect username",
            "incorrect password",
            "authentication required",
            "unauthorized to",
            "not authorized",
            "denied for",
            "may require 'docker login",
            'may require "docker login',
            "no pull access",
            "failed to authorize",
        )
        has_auth_signal = any(m in describe for m in auth_in_describe) or any(
            s in d for s in auth_in_lower
        )
        # Fallback: pull stuck + creds-ish wording (common on Docker Desktop + Hub private)
        if not has_auth_signal:
            pull_stuck = "errimagepull" in compact or "imagepullbackoff" in compact
            creds_hint = any(
                x in d
                for x in (
                    "denied",
                    "unauthorized",
                    "authenticate",
                    "docker login",
                    "incorrect",
                    "credentials",
                    "401",
                    "403",
                )
            )
            failed_pull = "failed to pull" in d or "error pulling" in d
            if pull_stuck and failed_pull and creds_hint:
                has_auth_signal = True
        if not has_auth_signal:
            raise RuntimeError(
                "Validation failed: no auth/pull-denied markers in describe. "
                "If using Docker Desktop, try `kubectl describe pod` on a failed sample "
                "with --keep-namespaces; we can add more substrings. Skip this sample."
            )
    else:
        raise KeyError(scenario_id)


def wait_until_failure_visible(
    ns: str,
    pod: str,
    scenario_id: str,
    timeout_s: int = 180,
) -> None:
    """Poll until describe/events suggest the scenario is visible (CrashLoop, OOM, Pull)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        _, desc, _ = kubectl(["describe", "pod", pod, "-n", ns])
        blob = desc.lower()
        if scenario_id == "crashloop_bad_args" and "crashloopbackoff" in blob.replace(" ", "").lower():
            return
        if scenario_id == "oomkilled_limit_too_low" and "oomkilled" in blob.lower():
            return
        if scenario_id == "imagepull_registry_auth":
            b = desc
            if any(x in b for x in ("ImagePullBackOff", "ErrImagePull", "401", "Unauthorized", "failed to pull")):
                return
        time.sleep(3)
    # Best-effort: still collect data
    sys.stderr.write(
        f"Warning: timeout waiting for obvious failure markers for {scenario_id}; capturing anyway.\n"
    )


def run_scenario(
    scenario_id: str,
    workload_kind: str,
    workload_name: str,
    private_pull_image: str,
    keep_namespaces: bool = False,
) -> Dict[str, Any]:
    if scenario_id == "imagepull_registry_auth" and not (private_pull_image or "").strip():
        raise ValueError(
            "imagepull_registry_auth requires a private image ref via "
            "--private-pull-image or env RCA_PRIVATE_PULL_IMAGE."
        )

    ns = random_namespace()
    rc, _, err = kubectl(["create", "namespace", ns])
    if rc != 0:
        raise RuntimeError(f"create namespace failed: {err}")

    try:
        yaml_text = manifest_for_scenario(
            scenario_id, ns, workload_kind, workload_name, private_pull_image.strip()
        )
        kubectl_apply_yaml(yaml_text)

        pod = wait_for_pod_name(ns)
        # Give kubelet time to update status
        time.sleep(5)
        wait_until_failure_visible(ns, pod, scenario_id)

        pod_obj = get_pod_json(ns, pod)
        created_at, pod_status, waiting_reason, container_name, image = extract_pod_fields(pod_obj)

        get_pods, describe, events, logs = capture_outputs(ns, pod)
        validate_capture(scenario_id, describe, pod_obj)

        evidence_text = build_evidence_text(
            ns,
            workload_kind,
            workload_name,
            container_name,
            image,
            get_pods,
            describe,
            events,
            logs,
        )

        rem = remediation_for_scenario(scenario_id, workload_kind, workload_name)
        for _k in ("actions_text", "rollback_text"):
            rem[_k] = rem[_k].replace("<namespace>", ns).replace("<pod_name>", pod)

        row: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "schema_version": SCHEMA_VERSION,
            "data_origin": "real_k8s",
            "capture_validated": True,
            "namespace": ns,
            "pod_name": pod,
            "workload_kind": workload_kind,
            "workload_name": workload_name,
            "container_name": container_name,
            "image": image,
            "created_at": created_at,
            "pod_status": pod_status,
            "waiting_reason": waiting_reason,
            "scenario_id": scenario_id,
            "failure_class": FAILURE_CLASS[scenario_id],
            "get_pods_text": get_pods,
            "describe_text": describe,
            "events_text": events,
            "logs_text": logs,
            "evidence_text": evidence_text,
            "diagnosis_text": rem["diagnosis_text"],
            "fix_plan_text": rem["fix_plan_text"],
            "actions_text": rem["actions_text"],
            "verification_text": rem["verification_text"],
            "rollback_text": rem["rollback_text"],
        }
        return row
    finally:
        if not keep_namespaces:
            kubectl_delete_namespace(ns)


def main() -> None:
    require_kubectl()
    ap = argparse.ArgumentParser(description="Collect real K8s incidents (JSONL).")
    ap.add_argument(
        "--output",
        "-o",
        default=str(DEFAULT_OUTPUT_FILE),
        help=f"Output JSONL path (default: {DEFAULT_OUTPUT_FILE})",
    )
    ap.add_argument(
        "--append",
        action="store_true",
        help=(
            "Append new rows to the output file. Without this flag, the file is "
            "OVERWRITTEN each run (earlier lines are lost)."
        ),
    )
    ap.add_argument(
        "--scenarios",
        nargs="*",
        default=list(SCENARIO_IDS),
        choices=list(SCENARIO_IDS),
        help="Subset of scenarios to run (default: all three)",
    )
    ap.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Run each selected scenario this many times (new namespace each time)",
    )
    ap.add_argument(
        "--workload-kind",
        choices=("Deployment", "StatefulSet"),
        default="Deployment",
        help="Workload type to create for each scenario",
    )
    ap.add_argument(
        "--workload-name",
        default="demo-workload",
        help="Deployment/StatefulSet metadata.name",
    )
    ap.add_argument(
        "--private-pull-image",
        default="",
        help=(
            "Required for imagepull_registry_auth: a PRIVATE image reference "
            "(e.g. docker.io/YOURUSER/private-test:v1) that fails with invalid "
            "docker-hub creds. Override with env RCA_PRIVATE_PULL_IMAGE."
        ),
    )
    ap.add_argument(
        "--keep-namespaces",
        action="store_true",
        help="Do not delete scenario namespaces after each run (debug flaky failures)",
    )
    args = ap.parse_args()

    private_pull = (args.private_pull_image or "").strip() or os.environ.get(
        "RCA_PRIVATE_PULL_IMAGE", ""
    ).strip()
    if "imagepull_registry_auth" in args.scenarios and not private_pull:
        sys.exit(
            "ERROR: imagepull_registry_auth needs --private-pull-image or "
            "RCA_PRIVATE_PULL_IMAGE (a private image; public images often pull anyway)."
        )

    if "imagepull_registry_auth" in args.scenarios:
        print(
            "\n[imagepull_registry_auth] Environment / experiment setup (not enforced by code):\n"
            "  - Image ref must exist; tag spelling must match the registry.\n"
            "  - Repo must be PRIVATE: `docker logout` then `docker pull <ref>` should FAIL;\n"
            "    after `docker login`, `docker pull <ref>` should SUCCEED.\n"
            "  - This script’s fake Secret targets Docker Hub; GHCR/ECR need matching registry in Secret.\n"
            "  - Skips = no auth-specific text in describe, or pod pulled successfully.\n",
            file=sys.stderr,
            flush=True,
        )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not args.append and out_path.exists() and out_path.stat().st_size > 0:
        print(
            f"\nWARNING: Overwriting existing file (no --append): {out_path.resolve()}\n"
            "         Previous lines are discarded. Use --append to add to the file.\n",
            file=sys.stderr,
            flush=True,
        )
    mode = "a" if args.append else "w"
    verb = "Appended" if args.append else "Wrote"
    written = 0
    with open(out_path, mode, encoding="utf-8") as out_f:
        for sid in args.scenarios:
            for rep in range(args.repeats):
                workload_name = (
                    f"{args.workload_name}-{rep + 1}"
                    if args.repeats > 1
                    else args.workload_name
                )
                print(f"=== Scenario: {sid} ({rep + 1}/{args.repeats}) ===", flush=True)
                try:
                    row = run_scenario(
                        sid,
                        args.workload_kind,
                        workload_name,
                        private_pull,
                        keep_namespaces=args.keep_namespaces,
                    )
                except Exception as e:
                    print(f"SKIP: {e}", flush=True)
                    continue
                out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
                out_f.flush()
                written += 1
                print(
                    f"Captured id={row['id']} pod={row['pod_name']} namespace={row['namespace']}",
                    flush=True,
                )
    print(f"{verb} {written} rows to {out_path.resolve()}")


if __name__ == "__main__":
    main()
