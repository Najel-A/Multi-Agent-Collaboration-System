#!/usr/bin/env python3
import argparse
import json
import random
import string
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Optional

SCENARIOS = {
    "failedscheduling_insufficient_memory": {
        "description": "Pod requests more memory than any node can provide.",
        "requests": {"cpu": "100m", "memory": "1000Gi"},
        "limits": {"cpu": "100m", "memory": "1000Gi"},
        "node_selector": {},
        "diagnosis_text": "Pod could not be scheduled because its memory request exceeds the allocatable memory available on any node in the cluster.",
        "fix_plan_text": (
            "1. Inspect the pod scheduling events to confirm an insufficient memory condition.\n"
            "2. Reduce the memory requests/limits to realistic values or provision nodes with higher memory capacity.\n"
            "3. Re-apply the workload and confirm it schedules successfully."
        ),
        "actions_text": (
            "kubectl -n {namespace} describe pod {pod_name}\n"
            "kubectl -n {namespace} get events --sort-by=.lastTimestamp\n"
            "kubectl -n {namespace} get {workload_kind_lower} {workload_name} -o yaml\n"
            "# Edit resources.requests.memory / resources.limits.memory to fit cluster capacity\n"
            "kubectl -n {namespace} rollout restart {workload_kind_lower}/{workload_name}"
        ),
        "verification_text": (
            "1. `kubectl -n {namespace} get pods` shows the pod scheduled and progressing to Running.\n"
            "2. `kubectl -n {namespace} get events` no longer reports FailedScheduling due to insufficient memory."
        ),
        "rollback_text": (
            "If the new resource settings cause instability, revert to the previous workload manifest and restore the last known good deployment configuration."
        ),
    },
    "failedscheduling_insufficient_cpu": {
        "description": "Pod requests more CPU than any node can provide.",
        "requests": {"cpu": "1000", "memory": "128Mi"},
        "limits": {"cpu": "1000", "memory": "128Mi"},
        "node_selector": {},
        "diagnosis_text": "Pod could not be scheduled because its CPU request exceeds the allocatable CPU available on any node in the cluster.",
        "fix_plan_text": (
            "1. Inspect scheduling events to verify the insufficient CPU condition.\n"
            "2. Reduce CPU requests/limits or scale the cluster with nodes that provide sufficient CPU.\n"
            "3. Re-deploy the workload and verify successful scheduling."
        ),
        "actions_text": (
            "kubectl -n {namespace} describe pod {pod_name}\n"
            "kubectl -n {namespace} get events --sort-by=.lastTimestamp\n"
            "kubectl -n {namespace} get {workload_kind_lower} {workload_name} -o yaml\n"
            "# Edit resources.requests.cpu / resources.limits.cpu to fit cluster capacity\n"
            "kubectl -n {namespace} rollout restart {workload_kind_lower}/{workload_name}"
        ),
        "verification_text": (
            "1. `kubectl -n {namespace} get pods` shows the pod scheduled and progressing to Running.\n"
            "2. `kubectl -n {namespace} get events` no longer reports FailedScheduling due to insufficient CPU."
        ),
        "rollback_text": (
            "If reduced CPU settings degrade application performance, revert to the previous manifest and restore the original resource configuration."
        ),
    },
    "nodeselector_mismatch": {
        "description": "Pod nodeSelector matches no nodes in the cluster.",
        "requests": {"cpu": "100m", "memory": "128Mi"},
        "limits": {"cpu": "100m", "memory": "128Mi"},
        "node_selector": {"dedicated": "nonexistent-pool"},
        "diagnosis_text": "Pod could not be scheduled because the workload nodeSelector does not match any node labels in the cluster.",
        "fix_plan_text": (
            "1. Inspect scheduling events to confirm a node selector mismatch.\n"
            "2. Compare the workload nodeSelector with actual node labels.\n"
            "3. Update the nodeSelector to valid labels or remove it if unnecessary.\n"
            "4. Re-apply the workload and verify successful scheduling."
        ),
        "actions_text": (
            "kubectl -n {namespace} describe pod {pod_name}\n"
            "kubectl get nodes --show-labels\n"
            "kubectl -n {namespace} get {workload_kind_lower} {workload_name} -o yaml\n"
            "# Edit spec.template.spec.nodeSelector to match an existing node label\n"
            "kubectl -n {namespace} rollout restart {workload_kind_lower}/{workload_name}"
        ),
        "verification_text": (
            "1. `kubectl -n {namespace} get pods` shows the pod scheduled onto a node.\n"
            "2. `kubectl -n {namespace} get events` no longer reports FailedScheduling due to node selector mismatch."
        ),
        "rollback_text": (
            "If the updated node selector routes workloads to the wrong nodes, revert the selector to the previous value and redeploy the workload."
        ),
    },
}


def run(cmd, check=True, stdin_text=None):
    result = subprocess.run(
        cmd,
        input=stdin_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def rand_suffix(n=6):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def random_namespace():
    prefixes = ["team-a-dev", "team-b-stg", "payments-dev", "orders-prod", "infra-lab"]
    return f"{random.choice(prefixes)}-{rand_suffix()}"


# --- Workload identity (image, container, name) --------------------------------
#
# Dataset quality depends on more than random namespace/workload labels: if every
# incident uses the same container image and container name, we mostly exercise
# "the same app with different strings." That weakens diversity for models and
# evals that should generalize across realistic workload identities. We therefore
# vary image repository, tag, container name, and workload name prefix together
# from coherent "app profiles," while keeping scheduling behavior unchanged.


# Tags applied across private-registry style images; keeps semver vs floating tags mixed.
_IMAGE_TAGS = ["1.0.0", "1.2.3", "2.0.1", "latest", "stable"]

# Each profile: logical app family, image repo (no tag), workload name prefix, and
# container name candidates (first entry is the best semantic match to the image).
APP_PROFILES = [
    {
        "id": "acme-backend",
        "image_repo": "ghcr.io/acme/backend",
        "tags": _IMAGE_TAGS,
        "workload_prefix": "backend",
        "container_names": ["backend", "main", "app"],
    },
    {
        "id": "acme-worker",
        "image_repo": "ghcr.io/acme/worker",
        "tags": _IMAGE_TAGS,
        "workload_prefix": "worker",
        "container_names": ["worker", "processor", "main"],
    },
    {
        "id": "acme-api",
        "image_repo": "ghcr.io/acme/api",
        "tags": _IMAGE_TAGS,
        "workload_prefix": "api",
        "container_names": ["api", "main", "app"],
    },
    {
        "id": "acme-service",
        "image_repo": "ghcr.io/acme/service",
        "tags": _IMAGE_TAGS,
        "workload_prefix": "service",
        "container_names": ["service", "main", "app"],
    },
    {
        "id": "nginx",
        "image_repo": "docker.io/library/nginx",
        "tags": _IMAGE_TAGS,
        "workload_prefix": "web",
        "container_names": ["main", "app", "sidecar"],
    },
    {
        "id": "busybox",
        "image_repo": "docker.io/library/busybox",
        "tags": _IMAGE_TAGS,
        "workload_prefix": "job",
        "container_names": ["worker", "processor", "main"],
    },
]


def _split_image_ref(full_image: str):
    """Split `repo:tag` into (repo, tag). Assumes last ':' separates tag (not digest-heavy paths)."""
    if ":" in full_image:
        repo, tag = full_image.rsplit(":", 1)
        return repo, tag
    return full_image, "latest"


def _profile_from_image_override(full_image: str) -> dict:
    """Build a single-profile dict so --image still gets coherent naming."""
    repo, tag = _split_image_ref(full_image)
    base = repo.split("/")[-1] or "app"
    return {
        "id": f"override:{base}",
        "image_repo": repo,
        "tags": [tag],
        "workload_prefix": base,
        "container_names": ["main", "app", "api", "backend", "worker"],
    }


def choose_app_profile(image_override: Optional[str] = None) -> dict:
    """Pick a random app profile, or a synthetic profile when the user pins one image."""
    if image_override:
        return _profile_from_image_override(image_override)
    return random.choice(APP_PROFILES)


def choose_image(profile: dict) -> str:
    """Full image reference with a tag from the profile (random tag when multiple)."""
    tag = random.choice(profile["tags"])
    return f"{profile['image_repo']}:{tag}"


def choose_container_name(profile: dict) -> str:
    """Realistic container name; list order prefers names aligned with the image family."""
    return random.choice(profile["container_names"])


def choose_workload_name(profile: dict) -> str:
    """Workload name encodes app role via prefix derived from the chosen profile."""
    return f"{profile['workload_prefix']}-w-{rand_suffix(4)}"


def choose_workload_kind():
    return random.choice(["Deployment", "StatefulSet"])


def build_manifest(namespace, scenario_id, workload_kind, workload_name, container_name, image):
    scenario = SCENARIOS[scenario_id]
    replicas = 1
    labels = {"app": workload_name}

    node_selector_lines = ""
    if scenario["node_selector"]:
        pairs = "\n".join([f"        {k}: {v}" for k, v in scenario["node_selector"].items()])
        node_selector_lines = f"\n      nodeSelector:\n{pairs}"

    if workload_kind == "Deployment":
        manifest = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {workload_name}
  namespace: {namespace}
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app: {workload_name}
  template:
    metadata:
      labels:
        app: {workload_name}
    spec:{node_selector_lines}
      containers:
      - name: {container_name}
        image: {image}
        resources:
          requests:
            cpu: {scenario["requests"]["cpu"]}
            memory: {scenario["requests"]["memory"]}
          limits:
            cpu: {scenario["limits"]["cpu"]}
            memory: {scenario["limits"]["memory"]}
"""
    else:
        # Minimal headless service required for StatefulSet
        manifest = f"""
apiVersion: v1
kind: Service
metadata:
  name: {workload_name}
  namespace: {namespace}
spec:
  clusterIP: None
  selector:
    app: {workload_name}
  ports:
  - port: 80
    targetPort: 80
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {workload_name}
  namespace: {namespace}
spec:
  serviceName: {workload_name}
  replicas: {replicas}
  selector:
    matchLabels:
      app: {workload_name}
  template:
    metadata:
      labels:
        app: {workload_name}
    spec:{node_selector_lines}
      containers:
      - name: {container_name}
        image: {image}
        resources:
          requests:
            cpu: {scenario["requests"]["cpu"]}
            memory: {scenario["requests"]["memory"]}
          limits:
            cpu: {scenario["limits"]["cpu"]}
            memory: {scenario["limits"]["memory"]}
"""
    return textwrap.dedent(manifest).strip() + "\n"


def kubectl_json(ns, args):
    stdout, _, _ = run(["kubectl", "-n", ns] + args + ["-o", "json"])
    return json.loads(stdout)


def wait_for_pod(ns, timeout_sec=90):
    start = time.time()
    while time.time() - start < timeout_sec:
        stdout, _, _ = run(["kubectl", "-n", ns, "get", "pods", "-o", "json"], check=False)
        if stdout:
            try:
                data = json.loads(stdout)
                items = data.get("items", [])
                if items:
                    return items[0]["metadata"]["name"]
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    raise TimeoutError(f"No pod found in namespace {ns} within {timeout_sec}s")


def get_pod_json(ns, pod_name):
    stdout, _, _ = run(["kubectl", "-n", ns, "get", "pod", pod_name, "-o", "json"])
    return json.loads(stdout)


def extract_status_fields(pod_json):
    status = pod_json.get("status", {})
    pod_status = status.get("phase", "")
    waiting_reason = ""

    for cs in status.get("containerStatuses", []) or []:
        state = cs.get("state", {})
        if "waiting" in state:
            waiting_reason = state["waiting"].get("reason", "")
            break

    return pod_status, waiting_reason


def get_workload_identity(pod_json):
    owner_refs = pod_json.get("metadata", {}).get("ownerReferences", []) or []
    if owner_refs:
        return owner_refs[0].get("kind", ""), owner_refs[0].get("name", "")
    return "", ""


def collect_text_outputs(ns, pod_name):
    get_pods, _, _ = run(["kubectl", "-n", ns, "get", "pods"], check=False)
    describe_pod, _, _ = run(["kubectl", "-n", ns, "describe", "pod", pod_name], check=False)
    get_events, _, _ = run(
        ["kubectl", "-n", ns, "get", "events", "--sort-by=.lastTimestamp"],
        check=False,
    )
    logs, stderr, rc = run(
        ["kubectl", "-n", ns, "logs", pod_name, "--all-containers=true"],
        check=False,
    )

    # For scheduling failures, logs are often empty or unavailable.
    if rc != 0 or not logs.strip():
        logs = stderr.strip() or "No container logs available."

    return get_pods, describe_pod, get_events, logs


def build_evidence_text(namespace, workload_name, container_name, image, get_pods, describe_pod, get_events, logs):
    return (
        f"namespace: {namespace}\n"
        f"workload: {workload_name}\n"
        f"container: {container_name}\n"
        f"image: {image}\n"
        f"=== kubectl get pods ===\n{get_pods}\n"
        f"=== kubectl describe pod ===\n{describe_pod}\n"
        f"=== kubectl get events ===\n{get_events}\n"
        f"=== container logs ===\n{logs}"
    )


def format_scenario_text(template, namespace, pod_name, workload_kind, workload_name):
    workload_kind_lower = workload_kind.lower()
    return template.format(
        namespace=namespace,
        pod_name=pod_name,
        workload_kind_lower=workload_kind_lower,
        workload_name=workload_name,
    )


def create_namespace(ns):
    run(["kubectl", "create", "namespace", ns])


def apply_manifest(manifest_text):
    run(["kubectl", "apply", "-f", "-"], stdin_text=manifest_text)


def cleanup_namespace(ns):
    run(["kubectl", "delete", "namespace", ns, "--wait=false"], check=False)


def collect_one_incident(scenario_id, image_override: Optional[str] = None):
    profile = choose_app_profile(image_override)
    image = choose_image(profile)
    namespace = random_namespace()
    workload_kind = choose_workload_kind()
    workload_name = choose_workload_name(profile)
    container_name = choose_container_name(profile)

    # Log identity for debugging runs without extending the JSONL schema.
    print(
        f"[INFO] app_profile={profile['id']} image={image} "
        f"container={container_name} workload={workload_name}"
    )

    create_namespace(namespace)
    manifest = build_manifest(
        namespace=namespace,
        scenario_id=scenario_id,
        workload_kind=workload_kind,
        workload_name=workload_name,
        container_name=container_name,
        image=image,
    )
    apply_manifest(manifest)

    pod_name = wait_for_pod(namespace)
    time.sleep(5)  # let events populate a bit more

    pod_json = get_pod_json(namespace, pod_name)

    created_at = pod_json.get("metadata", {}).get("creationTimestamp", "")
    pod_status, waiting_reason = extract_status_fields(pod_json)

    # For scheduling failures, owner refs may point to ReplicaSet for Deployments.
    actual_workload_kind, actual_workload_name = get_workload_identity(pod_json)
    if actual_workload_kind == "ReplicaSet":
        actual_workload_kind = "Deployment"
        actual_workload_name = workload_name
    elif not actual_workload_kind:
        actual_workload_kind = workload_kind
        actual_workload_name = workload_name

    get_pods, describe_pod, get_events, logs = collect_text_outputs(namespace, pod_name)
    evidence_text = build_evidence_text(
        namespace,
        actual_workload_name,
        container_name,
        image,
        get_pods,
        describe_pod,
        get_events,
        logs,
    )

    scenario = SCENARIOS[scenario_id]
    row = {
        "scenario_id": scenario_id,
        "namespace": namespace,
        "workload_kind": actual_workload_kind,
        "workload_name": actual_workload_name,
        "container_name": container_name,
        "image": image,
        "created_at": created_at,
        "pod_status": pod_status,
        "waiting_reason": waiting_reason,
        "evidence_text": evidence_text,
        "diagnosis_text": scenario["diagnosis_text"],
        "fix_plan_text": scenario["fix_plan_text"],
        "actions_text": format_scenario_text(
            scenario["actions_text"],
            namespace=namespace,
            pod_name=pod_name,
            workload_kind=actual_workload_kind,
            workload_name=actual_workload_name,
        ),
        "verification_text": format_scenario_text(
            scenario["verification_text"],
            namespace=namespace,
            pod_name=pod_name,
            workload_kind=actual_workload_kind,
            workload_name=actual_workload_name,
        ),
        "rollback_text": scenario["rollback_text"],
    }
    return row, namespace


def main():
    parser = argparse.ArgumentParser(description="Collect Kubernetes scheduling-failure incidents into JSONL.")
    parser.add_argument(
        "--output",
        default="k8s_incidents.jsonl",
        help="Output JSONL file",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="How many times to run each scenario",
    )
    parser.add_argument(
        "--image",
        default=None,
        help="Pin a single container image (repo:tag). Default: random image from the app profile pool.",
    )
    parser.add_argument(
        "--keep-namespaces",
        action="store_true",
        help="Do not delete namespaces after collection",
    )
    args = parser.parse_args()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Writing incidents to: {out_path.resolve()}")

    with out_path.open("a", encoding="utf-8") as f:
        for scenario_id in SCENARIOS.keys():
            for i in range(args.repeats):
                ns = None
                try:
                    print(f"[INFO] Running scenario={scenario_id} repeat={i+1}")
                    row, ns = collect_one_incident(scenario_id, args.image)
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    f.flush()
                    print(f"[OK] Saved incident for scenario={scenario_id} namespace={row['namespace']}")
                except Exception as e:
                    print(f"[ERROR] scenario={scenario_id} repeat={i+1}: {e}", file=sys.stderr)
                finally:
                    if ns and not args.keep_namespaces:
                        cleanup_namespace(ns)

    print("[DONE] Data collection complete.")


if __name__ == "__main__":
    main()
