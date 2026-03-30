#!/usr/bin/env python3
"""
Generate real Kubernetes incident rows for mk's three scenarios:
  dns_resolution_failure, service_connection_refused, quota_exceeded_pods

Requires: kubectl on PATH, cluster access (e.g. Docker Desktop Kubernetes).

Usage (test with 3 runs):
  python scripts/real_k8s_mk/generate_mk_samples.py --per-scenario 3

Full sprint target:
  python scripts/real_k8s_mk/generate_mk_samples.py --per-scenario 500 --out data/real_mk/mk_incidents.jsonl

Remediation text fields are placeholders until Akash (or you) fills real labels.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import string
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple


def _run(cmd: List[str], timeout: int = 300) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout or "", p.stderr or ""


def kubectl(args: List[str], timeout: int = 300) -> str:
    code, out, err = _run(["kubectl", *args], timeout=timeout)
    if code != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed ({code}): {err or out}")
    return out


def kubectl_allow_fail(args: List[str], timeout: int = 300) -> Tuple[int, str, str]:
    return _run(["kubectl", *args], timeout=timeout)


def rand_suffix(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def ns_name(prefix: str) -> str:
    return f"{prefix}-{rand_suffix(8)}"


def apply_yaml(yaml_text: str) -> None:
    p = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=yaml_text,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if p.returncode != 0:
        raise RuntimeError(f"kubectl apply failed: {p.stderr or p.stdout}")


def delete_namespace(name: str) -> None:
    kubectl_allow_fail(["delete", "namespace", name, "--wait=false"], timeout=60)
    # Namespace deletion is async; best-effort wait
    for _ in range(60):
        code, _, _ = kubectl_allow_fail(["get", "namespace", name], timeout=10)
        if code != 0:
            return
        time.sleep(1)


def wait_pod_ready(namespace: str, pod_name: str, timeout_s: int = 180) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        code, out, _ = kubectl_allow_fail(
            ["get", "pod", pod_name, "-n", namespace, "-o", "jsonpath={.status.conditions[?(@.type=='Ready')].status}"]
        )
        if code == 0 and "True" in out:
            return
        time.sleep(1)
    raise TimeoutError(f"Pod {pod_name} not Ready in time")


def placeholder_remediation(scenario_id: str) -> Dict[str, str]:
    return {
        "diagnosis_text": f"[TODO: Akash] Ground-truth diagnosis for {scenario_id}",
        "fix_plan_text": "[TODO: Akash] Step-by-step fix plan",
        "actions_text": "[TODO: Akash] kubectl / change actions",
        "verification_text": "[TODO: Akash] How to verify resolved",
        "rollback_text": "[TODO: Akash] Rollback steps",
    }


def build_row(
    scenario_id: str,
    namespace: str,
    workload_kind: str,
    workload_name: str,
    container_name: str,
    image: str,
    created_at: str,
    pod_status: str,
    waiting_reason: str,
    evidence_text: str,
) -> Dict[str, Any]:
    base = {
        "id": str(uuid.uuid4()),
        "scenario_id": scenario_id,
        "namespace": namespace,
        "workload_kind": workload_kind,
        "workload_name": workload_name,
        "container_name": container_name,
        "image": image,
        "created_at": created_at,
        "pod_status": pod_status,
        "waiting_reason": waiting_reason,
        "evidence_text": evidence_text,
        "meta_collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    base.update(placeholder_remediation(scenario_id))
    return base


def scenario_dns(namespace: str) -> Dict[str, Any]:
    fake_host = f"totally-fake-{rand_suffix(10)}.default.svc.cluster.local"
    job_name = "dns-fail-demo"
    yaml = f"""apiVersion: batch/v1
kind: Job
metadata:
  name: {job_name}
  namespace: {namespace}
spec:
  ttlSecondsAfterFinished: 600
  backoffLimit: 2
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: dns-test
        image: busybox:1.36
        command: ["sh", "-c", "nslookup {fake_host}"]
"""
    apply_yaml(yaml)

    deadline = time.time() + 180
    pod_name = ""
    while time.time() < deadline:
        code, out, _ = kubectl_allow_fail(
            ["get", "pods", "-n", namespace, "-l", f"job-name={job_name}", "-o", "jsonpath={.items[0].metadata.name}"]
        )
        if code == 0 and out.strip():
            pod_name = out.strip()
            phase = kubectl(["get", "pod", pod_name, "-n", namespace, "-o", "jsonpath={.status.phase}"]).strip()
            if phase in ("Failed", "Succeeded"):
                break
        time.sleep(1)

    if not pod_name:
        raise RuntimeError("DNS job: pod not found")

    # Prefer logs from the job selector (single stream)
    _, logs, _ = kubectl_allow_fail(["logs", f"job/{job_name}", "-n", namespace])
    describe = kubectl(["describe", "pod", pod_name, "-n", namespace])
    created_at = kubectl(
        ["get", "pod", pod_name, "-n", namespace, "-o", "jsonpath={.metadata.creationTimestamp}"]
    ).strip()

    status = kubectl(["get", "pod", pod_name, "-n", namespace, "-o", "jsonpath={.status.phase}"]).strip()
    # waiting_reason often empty for failed job; keep container terminated reason snippet
    wr = ""
    try:
        wr = kubectl(
            [
                "get",
                "pod",
                pod_name,
                "-n",
                namespace,
                "-o",
                "jsonpath={.status.containerStatuses[0].state.terminated.reason}",
            ]
        ).strip()
    except RuntimeError:
        wr = ""

    evidence = (
        "=== kubectl logs job/" + job_name + " ===\n"
        + logs
        + "\n=== kubectl describe pod ===\n"
        + describe
    )

    return build_row(
        scenario_id="dns_resolution_failure",
        namespace=namespace,
        workload_kind="Job",
        workload_name=job_name,
        container_name="dns-test",
        image="busybox:1.36",
        created_at=created_at,
        pod_status=status or "Unknown",
        waiting_reason=wr,
        evidence_text=evidence.strip(),
    )


def scenario_conn_refused(namespace: str) -> Dict[str, Any]:
    job_name = "conn-refused-demo"
    yaml = f"""apiVersion: v1
kind: Service
metadata:
  name: ghost-svc
  namespace: {namespace}
spec:
  ports:
  - port: 80
    targetPort: 80
  selector:
    app: no-such-app-xyz-999
---
apiVersion: batch/v1
kind: Job
metadata:
  name: {job_name}
  namespace: {namespace}
spec:
  ttlSecondsAfterFinished: 600
  backoffLimit: 2
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: client
        image: busybox:1.36
        command: ["sh", "-c", "wget -O- http://ghost-svc/ 2>&1"]
"""
    apply_yaml(yaml)

    deadline = time.time() + 180
    pod_name = ""
    while time.time() < deadline:
        code, out, _ = kubectl_allow_fail(
            ["get", "pods", "-n", namespace, "-l", f"job-name={job_name}", "-o", "jsonpath={.items[0].metadata.name}"]
        )
        if code == 0 and out.strip():
            pod_name = out.strip()
            phase = kubectl(["get", "pod", pod_name, "-n", namespace, "-o", "jsonpath={.status.phase}"]).strip()
            if phase in ("Failed", "Succeeded"):
                break
        time.sleep(1)
    if not pod_name:
        raise RuntimeError("Conn refused job: pod not found")

    _, logs, _ = kubectl_allow_fail(["logs", f"job/{job_name}", "-n", namespace])
    describe = kubectl(["describe", "pod", pod_name, "-n", namespace])
    created_at = kubectl(
        ["get", "pod", pod_name, "-n", namespace, "-o", "jsonpath={.metadata.creationTimestamp}"]
    ).strip()
    status = kubectl(["get", "pod", pod_name, "-n", namespace, "-o", "jsonpath={.status.phase}"]).strip()
    wr = ""
    try:
        wr = kubectl(
            [
                "get",
                "pod",
                pod_name,
                "-n",
                namespace,
                "-o",
                "jsonpath={.status.containerStatuses[0].state.terminated.reason}",
            ]
        ).strip()
    except RuntimeError:
        wr = ""

    evidence = (
        "=== kubectl logs job/" + job_name + " ===\n"
        + logs
        + "\n=== kubectl describe pod ===\n"
        + describe
    )
    return build_row(
        scenario_id="service_connection_refused",
        namespace=namespace,
        workload_kind="Job",
        workload_name=job_name,
        container_name="client",
        image="busybox:1.36",
        created_at=created_at,
        pod_status=status or "Unknown",
        waiting_reason=wr,
        evidence_text=evidence.strip(),
    )


def scenario_quota(namespace: str) -> Dict[str, Any]:
    yaml_quota_fillers = f"""apiVersion: v1
kind: ResourceQuota
metadata:
  name: pod-cap
  namespace: {namespace}
spec:
  hard:
    pods: "2"
---
apiVersion: v1
kind: Pod
metadata:
  name: filler-1
  namespace: {namespace}
spec:
  containers:
  - name: c
    image: busybox:1.36
    command: ["sleep", "3600"]
---
apiVersion: v1
kind: Pod
metadata:
  name: filler-2
  namespace: {namespace}
spec:
  containers:
  - name: c
    image: busybox:1.36
    command: ["sleep", "3600"]
"""
    apply_yaml(yaml_quota_fillers)
    wait_pod_ready(namespace, "filler-1")
    wait_pod_ready(namespace, "filler-2")

    dep_name = "blocked-deploy"
    yaml_dep = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {dep_name}
  namespace: {namespace}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: blocked
  template:
    metadata:
      labels:
        app: blocked
    spec:
      containers:
      - name: web
        image: nginx
"""
    apply_yaml(yaml_dep)

    time.sleep(8)

    events = kubectl(["get", "events", "-n", namespace, "--sort-by=.lastTimestamp"])
    dep_desc = kubectl(["describe", "deployment", dep_name, "-n", namespace])
    rs_name = (
        kubectl(
            ["get", "rs", "-n", namespace, "-l", f"app=blocked", "-o", "jsonpath={.items[0].metadata.name}"]
        ).strip()
    )
    rs_desc = kubectl(["describe", "rs", rs_name, "-n", namespace])

    created_at = kubectl(
        ["get", "deployment", dep_name, "-n", namespace, "-o", "jsonpath={.metadata.creationTimestamp}"]
    ).strip()

    evidence = (
        "=== kubectl get events ===\n"
        + events
        + "\n=== kubectl describe deployment ===\n"
        + dep_desc
        + "\n=== kubectl describe replicaset ===\n"
        + rs_desc
    )

    # No workload pod for blocked deploy — use deployment conditions for status fields
    return build_row(
        scenario_id="quota_exceeded_pods",
        namespace=namespace,
        workload_kind="Deployment",
        workload_name=dep_name,
        container_name="web",
        image="nginx",
        created_at=created_at,
        pod_status="ReplicaFailure",
        waiting_reason="FailedCreate",
        evidence_text=evidence.strip(),
    )


SCENARIOS = {
    "dns_resolution_failure": scenario_dns,
    "service_connection_refused": scenario_conn_refused,
    "quota_exceeded_pods": scenario_quota,
}


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate mk real K8s JSONL samples")
    ap.add_argument("--per-scenario", type=int, default=5, help="Samples per scenario (e.g. 500)")
    ap.add_argument(
        "--out",
        type=str,
        default="data/real_mk/mk_incidents.jsonl",
        help="Output JSONL path (append mode)",
    )
    ap.add_argument(
        "--scenarios",
        type=str,
        default="all",
        help="Comma list of scenario_ids or 'all'",
    )
    args = ap.parse_args()

    if args.per_scenario < 1:
        print("--per-scenario must be >= 1", file=sys.stderr)
        sys.exit(1)

    wanted = list(SCENARIOS.keys()) if args.scenarios.strip().lower() == "all" else [s.strip() for s in args.scenarios.split(",") if s.strip()]
    for s in wanted:
        if s not in SCENARIOS:
            print(f"Unknown scenario: {s}", file=sys.stderr)
            sys.exit(1)

    out_path = args.out
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    total = 0
    with open(out_path, "a", encoding="utf-8") as f:
        for scen in wanted:
            fn = SCENARIOS[scen]
            for i in range(args.per_scenario):
                namespace = ns_name("mk")
                kubectl(["create", "namespace", namespace])
                try:
                    row = fn(namespace)
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    f.flush()
                    total += 1
                    print(f"[ok] {scen} {i+1}/{args.per_scenario} id={row['id']} ns={namespace}")
                except Exception as e:
                    print(f"[fail] {scen} iter={i+1} ns={namespace}: {e}", file=sys.stderr)
                finally:
                    delete_namespace(namespace)
                    time.sleep(1)

    print(f"Wrote {total} rows -> {out_path}")


if __name__ == "__main__":
    main()
