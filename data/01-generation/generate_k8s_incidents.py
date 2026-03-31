#!/usr/bin/env python3
import argparse
import json
import random
import string
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Optional

SCENARIOS = {
    "createcontainerconfigerror_missing_secret": {
        "description": "Container references an environment variable from a Secret that does not exist.",
        "env_from_secret": True,
        "secret_name_template": "app-secret-{suffix}",
        "secret_key": "DATABASE_URL",
        "env_from_configmap": False,
        "configmap_name_template": None,
        "configmap_key": None,
        "bad_image_tag": False,
        "diagnosis_text": (
            "The container could not start because it references an environment variable "
            "sourced from a Kubernetes Secret that does not exist in the namespace. "
            "The pod is stuck in CreateContainerConfigError."
        ),
        "fix_plan_text": (
            "1. Inspect the pod events and container status to confirm a missing Secret reference.\n"
            "2. Identify the expected Secret name and key from the workload spec.\n"
            "3. Create the missing Secret with the required key, or update the workload to "
            "reference an existing Secret.\n"
            "4. The pod will automatically retry and transition to Running once the Secret is available."
        ),
        "actions_text": (
            "kubectl -n {namespace} describe pod {pod_name}\n"
            "kubectl -n {namespace} get events --sort-by=.lastTimestamp\n"
            "kubectl -n {namespace} get {workload_kind_lower} {workload_name} -o yaml\n"
            "kubectl -n {namespace} get secrets\n"
            "# Create the missing secret:\n"
            "kubectl -n {namespace} create secret generic <secret-name> --from-literal=<key>=<value>\n"
            "kubectl -n {namespace} rollout restart {workload_kind_lower}/{workload_name}"
        ),
        "verification_text": (
            "1. `kubectl -n {namespace} get pods` shows the pod transitioning to Running.\n"
            "2. `kubectl -n {namespace} get events` no longer reports CreateContainerConfigError.\n"
            "3. `kubectl -n {namespace} get secrets` confirms the required Secret exists."
        ),
        "rollback_text": (
            "If the newly created Secret contains incorrect values, delete it and recreate "
            "with the correct data, or revert the workload spec to remove the Secret reference."
        ),
    },
    "createcontainerconfigerror_bad_configmap_key": {
        "description": "Container references a key in a ConfigMap that exists, but the key does not.",
        "env_from_secret": False,
        "secret_name_template": None,
        "secret_key": None,
        "env_from_configmap": True,
        "configmap_name_template": "app-config-{suffix}",
        "configmap_key": "NONEXISTENT_KEY",
        "configmap_real_key": "APP_MODE",
        "configmap_real_value": "production",
        "bad_image_tag": False,
        "diagnosis_text": (
            "The container could not start because it references a key in a ConfigMap that "
            "does not exist. The ConfigMap itself is present in the namespace, but the "
            "specified key is missing. The pod is stuck in CreateContainerConfigError."
        ),
        "fix_plan_text": (
            "1. Inspect the pod events and container status to confirm the ConfigMap key error.\n"
            "2. List the keys in the referenced ConfigMap.\n"
            "3. Either add the missing key to the ConfigMap or update the workload to "
            "reference a key that exists.\n"
            "4. The pod will automatically retry and transition to Running once the key is available."
        ),
        "actions_text": (
            "kubectl -n {namespace} describe pod {pod_name}\n"
            "kubectl -n {namespace} get events --sort-by=.lastTimestamp\n"
            "kubectl -n {namespace} get {workload_kind_lower} {workload_name} -o yaml\n"
            "kubectl -n {namespace} get configmap <configmap-name> -o yaml\n"
            "# Add the missing key to the ConfigMap or fix the key reference in the workload spec\n"
            "kubectl -n {namespace} rollout restart {workload_kind_lower}/{workload_name}"
        ),
        "verification_text": (
            "1. `kubectl -n {namespace} get pods` shows the pod transitioning to Running.\n"
            "2. `kubectl -n {namespace} get events` no longer reports CreateContainerConfigError.\n"
            "3. `kubectl -n {namespace} describe configmap <configmap-name>` shows the required key."
        ),
        "rollback_text": (
            "If the ConfigMap key change introduces incorrect configuration, revert the "
            "ConfigMap to its previous state or update the workload spec to reference the "
            "correct key name."
        ),
    },
    "imagepull_bad_tag": {
        "description": "Container image references a tag that does not exist in the registry.",
        "env_from_secret": False,
        "secret_name_template": None,
        "secret_key": None,
        "env_from_configmap": False,
        "configmap_name_template": None,
        "configmap_key": None,
        "bad_image_tag": True,
        "diagnosis_text": (
            "The container could not start because the specified image tag does not exist "
            "in the container registry. The pod is stuck in ImagePullBackOff or ErrImagePull."
        ),
        "fix_plan_text": (
            "1. Inspect the pod events to confirm an image pull failure due to a bad tag.\n"
            "2. Check the container image reference in the workload spec.\n"
            "3. Verify available tags in the registry and update the image tag to a valid one.\n"
            "4. Re-deploy the workload with the corrected image reference."
        ),
        "actions_text": (
            "kubectl -n {namespace} describe pod {pod_name}\n"
            "kubectl -n {namespace} get events --sort-by=.lastTimestamp\n"
            "kubectl -n {namespace} get {workload_kind_lower} {workload_name} -o yaml\n"
            "# Verify available tags in the registry and update the image tag\n"
            "kubectl -n {namespace} set image {workload_kind_lower}/{workload_name} <container>=<image>:<valid-tag>\n"
            "kubectl -n {namespace} rollout restart {workload_kind_lower}/{workload_name}"
        ),
        "verification_text": (
            "1. `kubectl -n {namespace} get pods` shows the pod pulling the image and transitioning to Running.\n"
            "2. `kubectl -n {namespace} get events` no longer reports ErrImagePull or ImagePullBackOff.\n"
            "3. `kubectl -n {namespace} describe pod {pod_name}` shows the container started successfully."
        ),
        "rollback_text": (
            "If the updated image tag causes application issues, revert the workload to the "
            "previous known-good image tag and redeploy."
        ),
    },
}

# --- Bad image tags for imagepull_bad_tag scenario ---
BAD_IMAGE_TAGS = [
    "v99.99.99",
    "does-not-exist",
    "0.0.0-nonexistent",
    "release-20250101-missing",
    "sha-deadbeef",
    "nightly-fake",
]


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

_IMAGE_TAGS = ["1.0.0", "1.2.3", "2.0.1", "latest", "stable"]

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
    if ":" in full_image:
        repo, tag = full_image.rsplit(":", 1)
        return repo, tag
    return full_image, "latest"


def _profile_from_image_override(full_image: str) -> dict:
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
    if image_override:
        return _profile_from_image_override(image_override)
    return random.choice(APP_PROFILES)


def choose_image(profile: dict, bad_tag: bool = False) -> str:
    if bad_tag:
        return f"{profile['image_repo']}:{random.choice(BAD_IMAGE_TAGS)}"
    tag = random.choice(profile["tags"])
    return f"{profile['image_repo']}:{tag}"


def choose_container_name(profile: dict) -> str:
    return random.choice(profile["container_names"])


def choose_workload_name(profile: dict) -> str:
    return f"{profile['workload_prefix']}-w-{rand_suffix(4)}"


def choose_workload_kind():
    return random.choice(["Deployment", "StatefulSet"])


def build_manifest(namespace, scenario_id, workload_kind, workload_name, container_name, image,
                   secret_name=None, secret_key=None,
                   configmap_name=None, configmap_key=None):
    scenario = SCENARIOS[scenario_id]
    replicas = 1

    # Build env section based on scenario
    env_lines = ""
    if scenario["env_from_secret"]:
        env_lines = textwrap.dedent(f"""\
        env:
          - name: {secret_key}
            valueFrom:
              secretKeyRef:
                name: {secret_name}
                key: {secret_key}""")
    elif scenario["env_from_configmap"]:
        env_lines = textwrap.dedent(f"""\
        env:
          - name: {configmap_key}
            valueFrom:
              configMapKeyRef:
                name: {configmap_name}
                key: {configmap_key}""")

    env_block = ""
    if env_lines:
        env_block = "\n" + textwrap.indent(env_lines, "        ")

    if workload_kind == "Deployment":
        manifest = textwrap.dedent(f"""\
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
    spec:
      containers:
      - name: {container_name}
        image: {image}
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
          limits:
            cpu: 100m
            memory: 128Mi{env_block}
""")
    else:
        manifest = textwrap.dedent(f"""\
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
    spec:
      containers:
      - name: {container_name}
        image: {image}
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
          limits:
            cpu: 100m
            memory: 128Mi{env_block}
""")
    return manifest


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
    scenario = SCENARIOS[scenario_id]
    profile = choose_app_profile(image_override)
    image = choose_image(profile, bad_tag=scenario["bad_image_tag"])
    namespace = random_namespace()
    workload_kind = choose_workload_kind()
    workload_name = choose_workload_name(profile)
    container_name = choose_container_name(profile)

    secret_name = None
    secret_key = None
    configmap_name = None
    configmap_key = None

    if scenario["env_from_secret"]:
        secret_name = scenario["secret_name_template"].format(suffix=rand_suffix(4))
        secret_key = scenario["secret_key"]

    if scenario["env_from_configmap"]:
        configmap_name = scenario["configmap_name_template"].format(suffix=rand_suffix(4))
        configmap_key = scenario["configmap_key"]

    print(
        f"[INFO] app_profile={profile['id']} image={image} "
        f"container={container_name} workload={workload_name}"
    )

    create_namespace(namespace)

    # For bad_configmap_key scenario, create the ConfigMap (with a different key than referenced)
    if scenario["env_from_configmap"]:
        run([
            "kubectl", "-n", namespace, "create", "configmap", configmap_name,
            f"--from-literal={scenario['configmap_real_key']}={scenario['configmap_real_value']}",
        ])

    manifest = build_manifest(
        namespace=namespace,
        scenario_id=scenario_id,
        workload_kind=workload_kind,
        workload_name=workload_name,
        container_name=container_name,
        image=image,
        secret_name=secret_name,
        secret_key=secret_key,
        configmap_name=configmap_name,
        configmap_key=configmap_key,
    )
    apply_manifest(manifest)

    pod_name = wait_for_pod(namespace)
    # Give more time for image pull scenarios to surface errors
    wait_time = 15 if scenario["bad_image_tag"] else 5
    time.sleep(wait_time)

    pod_json = get_pod_json(namespace, pod_name)

    created_at = pod_json.get("metadata", {}).get("creationTimestamp", "")
    pod_status, waiting_reason = extract_status_fields(pod_json)

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
    parser = argparse.ArgumentParser(
        description="Collect Kubernetes config-error and image-pull incidents into JSONL."
    )
    parser.add_argument(
        "--output",
        default="k8s_config_incidents.jsonl",
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
