#!/usr/bin/env python3
"""
k8s_synth_generator_portfolio.py

Portfolio-quality synthetic Kubernetes incident dataset generator:
- 15–20 scenarios
- 400–600 samples per scenario (you choose)
- Total: 6,000–12,000 samples

Key feature: BALANCED generation.
Instead of sampling scenarios randomly, it generates a target count per scenario
(e.g., 500 each), so your dataset is evenly distributed.

Outputs:
  1) synthetic_source.jsonl  (full truth + observations + structured actions)
  2) synthetic_sft.jsonl     (prompt/completion pairs for SFT)
  3) stats.json              (scenario counts)

Run examples:
  python k8s_synth_generator_portfolio.py --per_scenario 500 --outdir ./data --seed 7
  python k8s_synth_generator_portfolio.py --per_scenario_min 400 --per_scenario_max 600 --outdir ./data
"""

import argparse
import json
import os
import random
import uuid
from datetime import datetime, timedelta


# ---------------------------
# Helpers
# ---------------------------

def now_utc():
    return datetime.utcnow()

def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def rand_name(prefix: str, k: int = 5) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return prefix + "-" + "".join(random.choice(alphabet) for _ in range(k))

def maybe(p: float) -> bool:
    return random.random() < p

def choice_weighted(items):
    """
    items: list of (value, weight)
    """
    total = sum(w for _, w in items)
    r = random.uniform(0, total)
    upto = 0.0
    for v, w in items:
        if upto + w >= r:
            return v
        upto += w
    return items[-1][0]


# ---------------------------
# Samplers
# ---------------------------

def sample_cluster_id():
    return f"cust-{random.randint(1, 350)}"

def sample_namespace():
    base = choice_weighted([
        ("default", 6),
        ("kube-system", 2),          # rare but realistic (careful if you don't want system namespaces)
        ("platform", 10),
        ("apps", 10),
        ("backend", 8),
        ("frontend", 6),
        ("data", 10),
        ("ml", 10),
        ("observability", 8),
        ("logging", 6),
        ("monitoring", 6),
        ("payments", 6),
        ("search", 6),
        ("edge", 4),
        ("staging", 6),
        ("prod", 4),
        ("dev", 4),
        ("sandbox", 4),
        ("team-a", 4),
        ("team-b", 4),
    ])

    # add variety without becoming nonsense
    if maybe(0.55) and base not in ("default", "kube-system"):
        suffix = choice_weighted([
            ("-dev", 20),
            ("-stg", 15),
            ("-prod", 10),
            (f"-{random.randint(1,9)}", 25),
            (f"-{rand_name('t', 2)}", 30),
        ])
        return base + suffix

    return base

def sample_workload_kind():
    return choice_weighted([
        ("Deployment", 78),
        ("StatefulSet", 12),
        ("Job", 5),
        ("DaemonSet", 5),
    ])

def sample_workload_name():
    base = choice_weighted([
        ("service", 25),
        ("app", 25),
        ("backend", 15),
        ("processor", 15),
        ("component", 10),
        ("module", 10),
    ])
    return f"{base}-{rand_name('w', 4)}"

def sample_container_name(workload_name):
    if maybe(0.6):
        return workload_name + "-" + random.choice(["c", "core", "svc"])
    return choice_weighted([
        ("main", 30),
        ("app", 30),
        ("worker", 20),
        ("server", 20),
    ])

def sample_image_repo():
    return choice_weighted([
        ("ghcr.io/acme/app", 35),
        ("ghcr.io/acme/service", 25),
        ("ghcr.io/acme/component", 20),
        ("docker.io/acme/app", 20),
    ])

def sample_image_for_workload(_workload_name=None):
    org = choice_weighted([("ghcr.io/acme", 45), ("docker.io/acme", 35), ("quay.io/acme", 20)])
    repo = choice_weighted([
        ("app", 20),
        ("service", 20),
        ("backend", 15),
        ("worker", 15),
        ("api", 15),
        ("component", 15),
    ])
    return f"{org}/{repo}"

def sample_tag():
    if maybe(0.15):
        return "latest"
    if maybe(0.10):
        return f"{random.randint(0,5)}.{random.randint(0,20)}"
    return f"{random.randint(0,5)}.{random.randint(0,20)}.{random.randint(0,30)}"

def sample_resources():
    mem_values = ["128Mi", "256Mi", "512Mi", "1Gi", "2Gi"]
    cpu_values = ["100m", "200m", "500m", "1", "2"]

    # choose request first
    req_mem = random.choice(mem_values)
    req_cpu = random.choice(cpu_values)

    # limit must be >= request
    mem_index = mem_values.index(req_mem)
    cpu_index = cpu_values.index(req_cpu)

    lim_mem = random.choice(mem_values[mem_index:])
    lim_cpu = random.choice(cpu_values[cpu_index:])

    return {
        "requests": {"cpu": req_cpu, "memory": req_mem},
        "limits": {"cpu": lim_cpu, "memory": lim_mem},
    }

def sample_nodes():
    n = random.randint(3, 9)
    nodes = []

    for _ in range(n):
        node = {
            "name": rand_name("node", 6),  # random node names
            "allocatable": {
                "cpu": random.choice(["4", "8", "16"]),
                "memory": random.choice(["16Gi", "32Gi", "64Gi"]),
                "nvidia.com/gpu": random.choice(["0", "0", "0", "1"]),
            },
            "labels": {
                "kubernetes.io/arch": random.choice(["amd64", "arm64"]),
                "nodepool": random.choice(["pool-a", "pool-b", "pool-c"]),
                "dedicated": random.choice(["batch", "gpu", "infra"]),
            },
            "taints": []
        }

        nodes.append(node)

    # Randomly assign control-plane taint to ONE random node sometimes
    if maybe(0.25):
        random.choice(nodes)["taints"].append({
            "key": "node-role.kubernetes.io/control-plane",
            "effect": "NoSchedule"
        })

    # Randomly assign dedicated taints independently
    for node in nodes:
        if maybe(0.12):
            node["taints"].append({
                "key": "dedicated",
                "value": random.choice(["gpu", "batch", "infra"]),
                "effect": "NoSchedule"
            })

    return nodes

def sample_secrets_present():
    base = [
        rand_name("secret", 4),
        rand_name("creds", 4),
        rand_name("token", 4),
        rand_name("config", 4),
        rand_name("auth", 4),
        rand_name("key", 4),
    ]

    present = set()
    for s in base:
        if maybe(0.6):
            present.add(s)

    if not present:
        present.add(random.choice(base))

    return sorted(present)

def sample_configmaps_present():
    # 1–5 configmaps typically
    num = random.randint(1, 5)
    cms = set()
    while len(cms) < num:
        cms.add(rand_name("cm", 5))  # e.g., cm-a8f1k
    return sorted(cms)

def sample_pvc_name_for_workload(workload_name):
    suffix = random.choice(["data", "cache", "uploads", "storage", "db"])
    # realistic-ish: <workload>-<suffix>
    return f"{workload_name}-{suffix}"

def sample_pvcs_present(workload_name=None):
    pvcs = set()

    # many namespaces have none
    num = choice_weighted([(0, 35), (1, 35), (2, 20), (3, 10)])

    for _ in range(num):
        if workload_name and maybe(0.7):
            pvcs.add(sample_pvc_name_for_workload(workload_name))
        else:
            pvcs.add(rand_name("pvc", 5))  # unrelated pvc

    return sorted(pvcs)


# ---------------------------
# World Builder
# ---------------------------

def build_world():
    cluster_id = sample_cluster_id()
    namespace = sample_namespace()

    wk = sample_workload_kind()

    # Make workload names neutral to prevent label leakage
    wn = sample_workload_name()  # recommended to return neutral like svc-xxxxx

    # Container name should not encode workload strongly
    cn = sample_container_name(wn)  # or ignore wn inside your sampler

    # Image should be independent of workload to avoid shortcut learning
    image = sample_image_for_workload(None)
    tag = sample_tag()

    replicas = random.randint(1, 5) if wk in ("Deployment", "StatefulSet") else 1

    nodes = sample_nodes()

    world = {
        "cluster_id": cluster_id,
        "namespace": namespace,
        "nodes": nodes,
        "inventory": {
            "secrets_present": sample_secrets_present(),
            "configmaps_present": sample_configmaps_present(),
            # PVCs will be overwritten below to incorporate workload naming realism
            "pvcs_present": [],
            "storageclasses_present": ["standard"] if maybe(0.85) else [],
            "quota": {
                "pods_limit": choice_weighted([(50, 60), (100, 30), (10, 10)]),
                "pods_used": 0,  # scenario may set
            }
        },
        "workload": {
            "kind": wk,
            "name": wn,
            "replicas": replicas,
            "container": {
                "name": cn,
                "image": image,
                "tag": tag,
                "resources": sample_resources(),  # must enforce requests<=limits in that function
            },
            "env": [],
            "args": [],
            "volumes": [],
            "serviceAccountName": choice_weighted([("default", 60), ("app-sa", 25), ("restricted-sa", 15)]),
            "nodeSelector": {} if maybe(0.85) else {"dedicated": random.choice(["batch", "gpu", "infra"])},
            "tolerations": []
        }
    }

    # --- PVC naming realism (without leaking scenario labels) ---
    pvcs_present = set()

    if wk == "StatefulSet":
        # simulate volumeClaimTemplate "data" -> PVCs like data-<podname>
        # podname typically includes workload + ordinal, we approximate with workload + ordinal
        for ordinal in range(replicas):
            pvcs_present.add(f"data-{wn}-{ordinal}")

        # stateful workloads often mount one of these
        if maybe(0.7):
            world["workload"]["volumes"].append({
                "name": "data",
                "persistentVolumeClaim": {"claimName": f"data-{wn}-0"}  # pod-0 claim (approx)
            })

    elif wk == "Deployment":
        # some deployments use a single shared PVC
        if maybe(0.35):
            pvc_name = f"{wn}-data"
            pvcs_present.add(pvc_name)
            world["workload"]["volumes"].append({
                "name": "data",
                "persistentVolumeClaim": {"claimName": pvc_name}
            })

    # Add a couple unrelated PVCs sometimes (multi-tenant namespace realism)
    for _ in range(choice_weighted([(0, 60), (1, 30), (2, 10)])):
        pvcs_present.add(rand_name("pvc", 5))

    world["inventory"]["pvcs_present"] = sorted(pvcs_present)

    return world


# ---------------------------
# Renderers (signals)
# ---------------------------

def render_kubectl_get_pods(world, status, ready="0/1", restarts=0, age="2m"):
    w = world["workload"]
    pod_name = f"{w['name']}-{rand_name('pod',4)}"
    table = (
        "NAME\tREADY\tSTATUS\tRESTARTS\tAGE\n"
        f"{pod_name}\t{ready}\t{status}\t{restarts}\t{age}\n"
    )
    return pod_name, table

def render_events_table(event_rows):
    header = "LAST SEEN\tTYPE\tREASON\tOBJECT\tMESSAGE\n"
    lines = [header]
    for r in event_rows:
        lines.append(f"{r['last_seen']}\t{r['type']}\t{r['reason']}\t{r['object']}\t{r['message']}")
    return "\n".join(lines).rstrip() + "\n"

def render_describe_pod(
    world,
    pod_name,
    pod_phase="Running",
    container_state="Waiting",
    reason="CrashLoopBackOff",
    restarts=0,
    events_table="",
    last_state=None,
    exit_code=None,
    message=None,
    ready=False,
    include_node=True,
):
    ns = world["namespace"]
    w = world["workload"]
    start = now_utc() - timedelta(minutes=random.randint(2, 15))

    lines = [
        f"Name:           {pod_name}",
        f"Namespace:      {ns}",
        "Priority:       0",
    ]

    if include_node:
        node = random.choice(world["nodes"])["name"]
        lines.append(f"Node:           {node}")

    lines.extend([
        f"Start Time:     {ts(start)}",
        f"Labels:         app={w['name']}",
        f"Status:         {pod_phase}",
        "Containers:",
        f"  {w['container']['name']}:",
        f"    Image:      {w['container']['image']}:{w['container']['tag']}",
        f"    State:      {container_state}",
    ])

    if reason:
        lines.append(f"      Reason:   {reason}")
    if message:
        lines.append(f"      Message:  {message}")

    if last_state:
        lines.append(f"    Last State: {last_state}")
        if exit_code is not None:
            lines.append(f"      Exit Code: {exit_code}")

    lines.extend([
        f"    Ready:      {'True' if ready else 'False'}",
        f"    Restart Count: {restarts}",
        "Events:",
        events_table.rstrip()
    ])

    return "\n".join(lines) + "\n"

def render_logs(base: datetime, lines_with_offsets):
    lines = []
    for off, msg in sorted(lines_with_offsets, key=lambda x: x[0]):
        lines.append(f"{ts(base + timedelta(seconds=off))} {msg}")
    return "\n".join(lines).rstrip() + "\n"

def waiting_logs_error(world, pod_name, reason):
    return (
        f'Error from server (BadRequest): container "{world["workload"]["container"]["name"]}" '
        f'in pod "{pod_name}" is waiting to start: {reason}\n'
    )


def add_noise_logs(log_txt, base_dt):
    # use only for real runtime scenarios
    extra = []
    last_off = 25
    for _ in range(choice_weighted([(0, 25), (1, 50), (2, 25)])):
        last_off += random.randint(1, 8)
        extra.append((last_off, f"INFO healthcheck ok component={random.choice(['db','cache','http','auth'])}"))
    if not extra:
        return log_txt

    lines = log_txt.rstrip().splitlines()
    for off, msg in sorted(extra, key=lambda x: x[0]):
        lines.append(f"{ts(base_dt + timedelta(seconds=off))} {msg}")
    return "\n".join(lines).rstrip() + "\n"

def rollout_restart_cmd(ns, world):
    w = world["workload"]
    kind = w.get("kind", "Deployment").lower()
    name = w["name"]

    kind_map = {
        "deployment": "deployment",
        "deploy": "deployment",
        "statefulset": "statefulset",
        "sts": "statefulset",
        "daemonset": "daemonset",
        "ds": "daemonset",
        "job": "job",
    }
    target_kind = kind_map.get(kind, "deployment")
    return f"kubectl -n {ns} rollout restart {target_kind}/{name}"

# ---------------------------
# Validators (lightweight but important)
# ---------------------------

def infer_failure_phase(sample):
    obs = sample.get("observations", {})
    rem = sample.get("remediation", {})
    fault = sample.get("fault", {})

    signals = " ".join([
        obs.get("kubectl_get_pods", ""),
        obs.get("kubectl_describe_pod", ""),
        obs.get("kubectl_get_events", ""),
        obs.get("container_logs", ""),
        rem.get("category", ""),
        rem.get("diagnosis", ""),
        fault.get("scenario_id", ""),
        fault.get("variant", ""),
    ]).lower()

    if "failedcreate" in signals or "quota" in signals:
        return "controller"
    if (
        "failedscheduling" in signals
        or "untolerated taint" in signals
        or "insufficient memory" in signals
        or "insufficient cpu" in signals
        or "didn't match pod's node affinity/selector" in signals
    ):
        return "scheduling"
    if (
        "failedmount" in signals
        or "persistentvolumeclaim" in signals
        or "unbound immediate persistentvolumeclaims" in signals
        or "storageclass" in signals
    ):
        return "mount"
    if "createcontainerconfigerror" in signals or "imagepullbackoff" in signals or "errimagepull" in signals:
        return "prestart"
    if "readiness probe failed" in signals or rem.get("category", "").lower() == "notready":
        return "notready"
    return "runtime"


def pack_sample(world, fault, observations, remediation, meta=None):
    w = world["workload"]

    sample = {
        "id": str(uuid.uuid4()),
        "context": {
            "cluster_id": world["cluster_id"],
            "namespace": world["namespace"],
            "workload_kind": w["kind"],
            "workload_name": w["name"],
            "container_name": w["container"]["name"],
            "image": f"{w['container']['image']}:{w['container']['tag']}",
        },
        "world": {
            "nodes": world["nodes"],
            "workload_spec": world["workload"],
            "inventory": world["inventory"],
        },
        "fault": fault,
        "observations": observations,
        "remediation": remediation,
        "meta": meta or {
            "created_at": ts(now_utc()),
            "difficulty": choice_weighted([("easy", 60), ("medium", 35), ("hard", 5)]),
            "noise_level": choice_weighted([(0, 25), (1, 50), (2, 25)]),
        }
    }

    sample["meta"]["failure_phase"] = infer_failure_phase(sample)
    return sample


def validate_sample(sample):
    obs = sample.get("observations", {})
    rem = sample.get("remediation", {})
    meta = sample.get("meta", {})

    if not obs.get("kubectl_get_pods") or not obs.get("kubectl_describe_pod"):
        return False, "missing core signals"

    diagnosis = (rem.get("diagnosis") or "").lower()
    signals_text = " ".join([
        obs.get("kubectl_get_pods", ""),
        obs.get("kubectl_describe_pod", ""),
        obs.get("kubectl_get_events", ""),
        obs.get("container_logs", ""),
        rem.get("category", ""),
    ]).lower()

    for kw in [
        "secret", "configmap", "manifest", "unauthorized", "taint", "toleration",
        "insufficient", "oom", "storageclass", "pvc", "forbidden", "dns",
        "probe", "quota", "connection refused"
    ]:
        if kw in diagnosis and kw not in signals_text:
            return False, f"evidence missing for '{kw}'"

    phase = meta.get("failure_phase", "runtime")
    logs = (obs.get("container_logs") or "").lower()
    describe = (obs.get("kubectl_describe_pod") or "").lower()
    getpods = (obs.get("kubectl_get_pods") or "").lower()
    events = (obs.get("kubectl_get_events") or "").lower()

    def has_runtime_logs(txt):
        markers = [
            "info starting",
            "warn ",
            "error invalid",
            "dial tcp",
            "forbidden:",
            "lookup ",
            "connection refused",
            "healthcheck ok",
            "http server started",
            "readiness returning 503",
            "killed",
            "exiting with code",
        ]
        return any(m in txt for m in markers)

    if phase == "prestart":
        if "restart count: 0" not in describe and "restart count: 1" not in describe:
            return False, "prestart failure should not have high restart count"
        if has_runtime_logs(logs):
            return False, "prestart failure should not have app runtime logs"

    if phase == "scheduling":
        if "\tpending\t" not in getpods and "status:         pending" not in describe:
            return False, "scheduling failure should be pending"
        if has_runtime_logs(logs):
            return False, "scheduling failure should not have runtime logs"
        if "failedscheduling" not in events:
            return False, "scheduling failure missing FailedScheduling event"

    if phase == "mount":
        if "failedmount" not in events and "persistentvolumeclaim" not in signals_text and "unbound immediate persistentvolumeclaims" not in signals_text:
            return False, "mount failure missing storage evidence"
        if has_runtime_logs(logs):
            return False, "mount failure should not have runtime logs"

    if phase == "notready":
        if "\trunning\t" not in getpods and "status:         running" not in describe:
            return False, "notready scenario should usually be running"
        if "ready:      false" not in describe:
            return False, "notready scenario should show Ready false"
        if "restart count: 0" not in describe and "restart count: 1" not in describe:
            return False, "notready scenario should not have high restarts"

    if phase == "controller":
        if "failedcreate" not in events and "quota" not in signals_text:
            return False, "controller failure missing create/quota evidence"

    if phase == "runtime":
        runtime_signal = has_runtime_logs(logs) or "last state: terminated" in describe or "crashloopbackoff" in signals_text
        if not runtime_signal:
            return False, "runtime failure missing runtime evidence"

    return True, None





# ---------------------------
# Scenario Implementations (NO GITOPS; keep scenarios otherwise unchanged)
# ---------------------------

def scenario_crashloop_missing_secret(world):
    ns = world["namespace"]
    w = world["workload"]
    inv = world["inventory"]

    existing = set(inv["secrets_present"])
    pool = ["db-credentials", "payments-db", "api-secrets", "redis-auth", "kafka-sasl", "s3-keys"]
    missing_secret = random.choice([s for s in pool if s not in existing] or ["payments-db"])
    missing_key = random.choice(["TOKEN", "DB_PASSWORD", "PASSWORD", "SASL_PASSWORD"])

    w["env"].append({
        "name": missing_key,
        "valueFrom": {"secretKeyRef": {"name": missing_secret, "key": missing_key}}
    })

    restarts = 0
    pod_name, getpods = render_kubectl_get_pods(
        world,
        status="CreateContainerConfigError",
        ready="0/1",
        restarts=restarts,
        age=f"{random.randint(1,9)}m"
    )

    events_rows = [{
        "last_seen": "22s",
        "type": "Warning",
        "reason": "Failed",
        "object": f"pod/{pod_name}",
        "message": f'Error: secret "{missing_secret}" not found'
    }]
    events = render_events_table(events_rows)

    describe = render_describe_pod(
        world,
        pod_name=pod_name,
        pod_phase="Pending",
        container_state="Waiting",
        reason="CreateContainerConfigError",
        restarts=restarts,
        events_table=events,
        last_state=None,
        exit_code=None,
        message=f'Error: secret "{missing_secret}" not found',
        ready=False
    )

    logs = waiting_logs_error(world, pod_name, "CreateContainerConfigError")

    diagnosis = (
        f"Pod is stuck in CreateContainerConfigError because the workload references Secret "
        f"`{missing_secret}` (key `{missing_key}`) that does not exist in namespace `{ns}`."
    )

    fix_plan = [
        f"Confirm the workload references Secret `{missing_secret}` key `{missing_key}`.",
        f"Create or restore Secret `{missing_secret}` with key `{missing_key}`, or update the workload to use the correct existing Secret.",
        "Restart or recreate the workload after correcting the reference.",
        "Verify the pod becomes Ready and events stop reporting secret-not-found."
    ]

    workload_ref = f"{w['kind'].lower()}/{w['name']}"
    restart_cmd = rollout_restart_cmd(ns, world) if w["kind"] != "Job" else f"kubectl -n {ns} delete pod {pod_name}"

    actions = [
        {"type": "kubectl_get_pods", "cmd": f"kubectl -n {ns} get pods"},
        {"type": "kubectl_describe_pod", "cmd": f"kubectl -n {ns} describe pod {pod_name}"},
        {"type": "kubectl_get_events", "cmd": f"kubectl -n {ns} get events --sort-by=.lastTimestamp"},
        {"type": "kubectl_get_workload", "cmd": f"kubectl -n {ns} get {workload_ref} -o yaml"},
        {"type": "kubectl_check_secret", "cmd": f"kubectl -n {ns} get secret {missing_secret}"},
        {"type": "kubectl_create_secret", "cmd": f"kubectl -n {ns} create secret generic {missing_secret} --from-literal={missing_key}=REDACTED"},
        {"type": "kubectl_restart_or_recreate", "cmd": restart_cmd},
    ]

    verification = [
        f"`kubectl -n {ns} get pods` shows the pod Ready.",
        f"`kubectl -n {ns} get events` no longer shows secret-not-found for `{missing_secret}`."
    ]

    rollback = [
        "Delete the incorrect secret if one was created by mistake and restore the approved source of truth."
    ]

    return pack_sample(
        world,
        fault={
            "scenario_id": "createcontainerconfigerror_missing_secret",
            "variant": "secret_not_found",
            "fault_params": {"missing_secret": missing_secret, "missing_key": missing_key}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {
                "restarts": restarts,
                "oom_killed": False
            }
        },
        remediation={
            "category": "CreateContainerConfigError",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": verification,
            "rollback": rollback
        }
    )

def scenario_crashloop_bad_configmap_key(world):
    ns = world["namespace"]
    w = world["workload"]
    inv = world["inventory"]

    cm = random.choice(inv["configmaps_present"])
    bad_key = f"MISSING_{random.choice(['CONFIG_PATH', 'MODE', 'FEATURE_X', 'LOG_LEVEL'])}"

    w["env"].append({
        "name": bad_key,
        "valueFrom": {
            "configMapKeyRef": {
                "name": cm,
                "key": bad_key
            }
        }
    })

    restarts = 0
    pod_name, getpods = render_kubectl_get_pods(
        world, "CreateContainerConfigError", "0/1", restarts, f"{random.randint(1,9)}m"
    )

    events = render_events_table([{
        "last_seen": "12s",
        "type": "Warning",
        "reason": "Failed",
        "object": f"pod/{pod_name}",
        "message": f'Error: configmap "{cm}" does not contain key "{bad_key}"'
    }])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Pending",
        container_state="Waiting",
        reason="CreateContainerConfigError",
        restarts=restarts,
        events_table=events,
        last_state=None,
        exit_code=None,
        message=f'Error: configmap "{cm}" does not contain key "{bad_key}"',
        ready=False
    )

    logs = waiting_logs_error(world, pod_name, "CreateContainerConfigError")

    diagnosis = f'Pod cannot start because ConfigMap `{cm}` is missing key `{bad_key}`.'
    fix_plan = [
        f'Add key `{bad_key}` to ConfigMap `{cm}` or update the workload to reference an existing key.',
        "Restart or recreate the workload so a new Pod is created.",
        f"Verify the Pod becomes Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_get_workload", "cmd": f"kubectl -n {ns} get {w['kind'].lower()}/{w['name']} -o yaml"},
        {"type": "kubectl_edit_configmap", "cmd": f"kubectl -n {ns} edit configmap {cm}"},
        {"type": "kubectl_rollout_restart", "cmd": rollout_restart_cmd(ns, world)},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods && kubectl -n {ns} get events --sort-by=.lastTimestamp"}
    ]

    rollback = [f"Restore the prior ConfigMap `{cm}` contents and restart again if needed."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "createcontainerconfigerror_bad_configmap_key",
            "variant": "configmap_key_missing",
            "fault_params": {"configmap": cm, "missing_key": bad_key}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": restarts, "oom_killed": False}
        },
        remediation={
            "category": "CreateContainerConfigError",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": [f"Pod Ready in `{ns}`; no configmap-key warnings remain."],
            "rollback": rollback
        }
    )

def scenario_crashloop_bad_args(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    bad_flag = random.choice(["--port=notanint", "--mode=invalid", "--threads=-1", "--config=/nope.yaml"])
    w["args"] = [bad_flag]

    restarts = random.randint(2, 12)
    pod_name, getpods = render_kubectl_get_pods(world, "CrashLoopBackOff", "0/1", restarts, f"{random.randint(2,10)}m")

    events = render_events_table([{
        "last_seen": "20s",
        "type": "Warning",
        "reason": "BackOff",
        "object": f"pod/{pod_name}",
        "message": "Back-off restarting failed container"
    }])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Running",
        container_state="Waiting",
        reason="CrashLoopBackOff",
        restarts=restarts,
        events_table=events,
        last_state="Terminated",
        exit_code=2,
        message="Back-off restarting failed container",
        ready=False
    )

    logs = render_logs(base, [
        (0, f"INFO starting {w['name']}"),
        (3, f"ERROR invalid argument: {bad_flag}"),
        (4, "ERROR exiting with code 2"),
    ])
    logs = add_noise_logs(logs, base)

    diagnosis = f"Container starts but exits immediately because the workload passes invalid args (`{bad_flag}`), causing CrashLoopBackOff."
    fix_plan = [
        "Correct the container args or command in the workload spec.",
        "Apply the change and restart the workload.",
        f"Verify restarts stop increasing and the Pod becomes Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_get_workload", "cmd": f"kubectl -n {ns} get {w['kind'].lower()}/{w['name']} -o yaml"},
        {"type": "kubectl_edit_workload", "cmd": f"kubectl -n {ns} edit {w['kind'].lower()}/{w['name']}"},
        {"type": "kubectl_rollout_restart", "cmd": rollout_restart_cmd(ns, world)},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]

    rollback = ["Undo the args or command change if it breaks startup further."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "crashloop_bad_args",
            "variant": "invalid_args",
            "fault_params": {"bad_arg": bad_flag}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": restarts, "oom_killed": False}
        },
        remediation={
            "category": "CrashLoopBackOff",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": ["Restarts stop increasing; pod Ready."],
            "rollback": rollback
        }
    )

def scenario_imagepull_bad_tag(world):
    ns = world["namespace"]
    w = world["workload"]

    good_tag = w["container"]["tag"]
    bad_tag = good_tag + "-typo"
    w["container"]["tag"] = bad_tag

    pod_name, getpods = render_kubectl_get_pods(world, "ImagePullBackOff", "0/1", 0, f"{random.randint(1,6)}m")

    events = render_events_table([
        {
            "last_seen": "20s",
            "type": "Warning",
            "reason": "Failed",
            "object": f"pod/{pod_name}",
            "message": f'Failed to pull image "{w["container"]["image"]}:{bad_tag}": manifest unknown'
        },
        {
            "last_seen": "18s",
            "type": "Warning",
            "reason": "Failed",
            "object": f"pod/{pod_name}",
            "message": f'Error: ErrImagePull'
        },
        {
            "last_seen": "15s",
            "type": "Warning",
            "reason": "BackOff",
            "object": f"pod/{pod_name}",
            "message": f'Back-off pulling image "{w["container"]["image"]}:{bad_tag}"'
        }
    ])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Pending",
        container_state="Waiting",
        reason="ImagePullBackOff",
        restarts=0,
        events_table=events,
        last_state=None,
        exit_code=None,
        message="Back-off pulling image",
        ready=False
    )

    logs = waiting_logs_error(world, pod_name, "ImagePullBackOff")

    diagnosis = f'Pod cannot start because image `{w["container"]["image"]}:{bad_tag}` does not exist in the registry (manifest unknown).'
    fix_plan = [
        f"Update the image tag from `{bad_tag}` to a valid tag such as `{good_tag}`.",
        "Apply the change and wait for a new Pod to pull the corrected image.",
        f"Verify the Pod becomes Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_set_image", "cmd": f"kubectl -n {ns} set image {w['kind'].lower()}/{w['name']} {w['container']['name']}={w['container']['image']}:{good_tag}"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods && kubectl -n {ns} get events --sort-by=.lastTimestamp"}
    ]

    rollback = ["Set the image back to the last known good tag if the replacement is wrong."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "imagepull_bad_tag",
            "variant": "manifest_unknown",
            "fault_params": {"bad_tag": bad_tag, "good_tag": good_tag}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": 0, "oom_killed": False}
        },
        remediation={
            "category": "ImagePullBackOff",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": [f"Pod Ready in `{ns}`; image pulls successfully."],
            "rollback": rollback
        }
    )

def scenario_imagepull_registry_auth(world):
    ns = world["namespace"]
    w = world["workload"]
    inv = world["inventory"]

    w["container"]["image"] = choice_weighted([("ghcr.io/acme/private-api", 60), ("docker.io/acme/private-worker", 40)])
    needed_secret = "registry-creds"
    inv["secrets_present"] = [s for s in inv["secrets_present"] if s != needed_secret]

    pod_name, getpods = render_kubectl_get_pods(world, "ImagePullBackOff", "0/1", 0, f"{random.randint(1,6)}m")

    events = render_events_table([
        {
            "last_seen": "25s",
            "type": "Warning",
            "reason": "Failed",
            "object": f"pod/{pod_name}",
            "message": f'Failed to pull image "{w["container"]["image"]}:{w["container"]["tag"]}": unauthorized: authentication required'
        },
        {
            "last_seen": "20s",
            "type": "Warning",
            "reason": "BackOff",
            "object": f"pod/{pod_name}",
            "message": f'Back-off pulling image "{w["container"]["image"]}:{w["container"]["tag"]}"'
        }
    ])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Pending",
        container_state="Waiting",
        reason="ImagePullBackOff",
        restarts=0,
        events_table=events,
        last_state=None,
        exit_code=None,
        message="unauthorized: authentication required",
        ready=False
    )

    logs = waiting_logs_error(world, pod_name, "ImagePullBackOff")

    diagnosis = "Image pull fails due to missing or invalid registry credentials (unauthorized)."
    fix_plan = [
        f"Create or repair imagePullSecret `{needed_secret}` in `{ns}`.",
        "Reference it from the ServiceAccount or Pod spec.",
        "Recreate or restart the workload and verify the image pull succeeds."
    ]

    actions = [
        {"type": "kubectl_create_registry_secret", "cmd": f"kubectl -n {ns} create secret docker-registry {needed_secret} --docker-server=... --docker-username=... --docker-password=REDACTED"},
        {"type": "kubectl_patch_sa", "cmd": f"kubectl -n {ns} patch serviceaccount {w['serviceAccountName']} -p '{{\"imagePullSecrets\":[{{\"name\":\"{needed_secret}\"}}]}}'"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]

    rollback = ["Remove the incorrect imagePullSecret reference or restore the previous working credential."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "imagepull_registry_auth",
            "variant": "unauthorized",
            "fault_params": {"secret_needed": needed_secret}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": 0, "oom_killed": False}
        },
        remediation={
            "category": "ImagePullBackOff",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": [f"Pod Ready in `{ns}`; no unauthorized image pull errors remain."],
            "rollback": rollback
        }
    )

def scenario_failedscheduling_taint(world):
    ns = world["namespace"]
    w = world["workload"]

    taint_key = "dedicated"
    taint_val = random.choice(["infra", "gpu", "batch"])
    for node in world["nodes"]:
        node["taints"] = [t for t in node["taints"] if t.get("key") != taint_key]
        if maybe(0.8):
            node["taints"].append({"key": taint_key, "value": taint_val, "effect": "NoSchedule"})
    w["tolerations"] = []

    pod_name, getpods = render_kubectl_get_pods(world, "Pending", "0/1", 0, f"{random.randint(1,10)}m")

    events = render_events_table([{
        "last_seen": "8s",
        "type": "Warning",
        "reason": "FailedScheduling",
        "object": f"pod/{pod_name}",
        "message": f"0/{len(world['nodes'])} nodes are available: {len(world['nodes'])} node(s) had untolerated taint {{{taint_key}={taint_val}: NoSchedule}}."
    }])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Pending",
        container_state="Waiting",
        reason="Pending",
        restarts=0,
        events_table=events,
        last_state=None,
        exit_code=None,
        message="FailedScheduling",
        ready=False
    )

    logs = waiting_logs_error(world, pod_name, "ContainerCreating")

    diagnosis = f"Pod cannot schedule due to untolerated taint `{taint_key}={taint_val}:NoSchedule`."
    fix_plan = [
        f"Add a toleration for `{taint_key}={taint_val}:NoSchedule`, or adjust node placement strategy.",
        "Apply the workload change and allow the scheduler to place the Pod.",
        f"Verify the Pod becomes Running and Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_get_nodes", "cmd": "kubectl get nodes -o json"},
        {"type": "kubectl_patch_workload", "cmd": f"kubectl -n {ns} patch {w['kind'].lower()}/{w['name']} --type merge -p '<tolerations patch>'"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]

    rollback = ["Remove the toleration if it places the workload on the wrong node pool."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "failedscheduling_taint",
            "variant": "untolerated_taint",
            "fault_params": {"taint_key": taint_key, "taint_value": taint_val}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": 0, "oom_killed": False}
        },
        remediation={
            "category": "FailedScheduling",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": [f"Pod scheduled and Running in `{ns}`."],
            "rollback": rollback
        }
    )

def scenario_failedscheduling_insufficient_memory(world):
    ns = world["namespace"]
    w = world["workload"]

    w["container"]["resources"]["requests"]["memory"] = "128Gi"

    pod_name, getpods = render_kubectl_get_pods(world, "Pending", "0/1", 0, f"{random.randint(1,10)}m")

    events = render_events_table([{
        "last_seen": "9s",
        "type": "Warning",
        "reason": "FailedScheduling",
        "object": f"pod/{pod_name}",
        "message": f"0/{len(world['nodes'])} nodes are available: {len(world['nodes'])} Insufficient memory."
    }])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Pending",
        container_state="Waiting",
        reason="Pending",
        restarts=0,
        events_table=events,
        last_state=None,
        exit_code=None,
        message="Insufficient memory",
        ready=False
    )

    logs = waiting_logs_error(world, pod_name, "ContainerCreating")

    diagnosis = "Pod cannot schedule because its memory request exceeds the available node capacity (Insufficient memory)."
    new_req = choice_weighted([("256Mi", 15), ("512Mi", 35), ("1Gi", 35), ("2Gi", 15)])
    fix_plan = [
        f"Reduce the memory request to a realistic value such as `{new_req}`, or add nodes with more memory.",
        "Apply the resource change and let the scheduler retry placement.",
        f"Verify the Pod becomes Running and Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_patch_resources", "cmd": f"kubectl -n {ns} patch {w['kind'].lower()}/{w['name']} --type merge -p '<resources patch>'"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]

    rollback = ["Restore the original resource request if the reduced memory value is insufficient."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "failedscheduling_insufficient_memory",
            "variant": "insufficient_memory",
            "fault_params": {"requested_memory": "128Gi", "suggested_memory": new_req}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": 0, "oom_killed": False}
        },
        remediation={
            "category": "FailedScheduling",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": [f"Pod scheduled and Running in `{ns}`."],
            "rollback": rollback
        }
    )

def scenario_failedscheduling_insufficient_cpu(world):
    ns = world["namespace"]
    w = world["workload"]

    w["container"]["resources"]["requests"]["cpu"] = "32"

    pod_name, getpods = render_kubectl_get_pods(world, "Pending", "0/1", 0, f"{random.randint(1,10)}m")

    events = render_events_table([{
        "last_seen": "11s",
        "type": "Warning",
        "reason": "FailedScheduling",
        "object": f"pod/{pod_name}",
        "message": f"0/{len(world['nodes'])} nodes are available: {len(world['nodes'])} Insufficient cpu."
    }])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Pending",
        container_state="Waiting",
        reason="Pending",
        restarts=0,
        events_table=events,
        last_state=None,
        exit_code=None,
        message="Insufficient cpu",
        ready=False
    )

    logs = waiting_logs_error(world, pod_name, "ContainerCreating")

    diagnosis = "Pod cannot schedule because its CPU request exceeds the available node capacity (Insufficient cpu)."
    new_req = choice_weighted([("200m", 20), ("500m", 40), ("1", 30), ("2", 10)])
    fix_plan = [
        f"Reduce the CPU request to a realistic value such as `{new_req}`, or add nodes with more CPU.",
        "Apply the resource change and let the scheduler retry placement.",
        f"Verify the Pod becomes Running and Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_patch_resources", "cmd": f"kubectl -n {ns} patch {w['kind'].lower()}/{w['name']} --type merge -p '<resources patch>'"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]

    rollback = ["Restore the original CPU request if the reduced value is incorrect."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "failedscheduling_insufficient_cpu",
            "variant": "insufficient_cpu",
            "fault_params": {"requested_cpu": "32", "suggested_cpu": new_req}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": 0, "oom_killed": False}
        },
        remediation={
            "category": "FailedScheduling",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": [f"Pod scheduled and Running in `{ns}`."],
            "rollback": rollback
        }
    )

def scenario_nodeselector_mismatch(world):
    ns = world["namespace"]
    w = world["workload"]

    w["nodeSelector"] = {"kubernetes.io/arch": "riscv64"}

    pod_name, getpods = render_kubectl_get_pods(world, "Pending", "0/1", 0, f"{random.randint(1,10)}m")

    events = render_events_table([{
        "last_seen": "9s",
        "type": "Warning",
        "reason": "FailedScheduling",
        "object": f"pod/{pod_name}",
        "message": f"0/{len(world['nodes'])} nodes are available: {len(world['nodes'])} node(s) didn't match Pod's node affinity/selector."
    }])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Pending",
        container_state="Waiting",
        reason="Pending",
        restarts=0,
        events_table=events,
        last_state=None,
        exit_code=None,
        message="nodeSelector mismatch",
        ready=False
    )

    logs = waiting_logs_error(world, pod_name, "ContainerCreating")

    diagnosis = "Pod cannot schedule because its nodeSelector or affinity does not match any nodes."
    fix_plan = [
        "Remove or correct the nodeSelector or node affinity so it matches real node labels.",
        "Apply the change and allow the scheduler to place the Pod.",
        f"Verify the Pod becomes Running and Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_get_nodes", "cmd": "kubectl get nodes --show-labels"},
        {"type": "kubectl_edit_workload", "cmd": f"kubectl -n {ns} edit {w['kind'].lower()}/{w['name']}"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]

    rollback = ["Restore the previous selector or affinity if it was required for correct placement."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "nodeselector_mismatch",
            "variant": "no_matching_nodes",
            "fault_params": {"nodeSelector": w["nodeSelector"]}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": 0, "oom_killed": False}
        },
        remediation={
            "category": "FailedScheduling",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": [f"Pod scheduled and Running in `{ns}`."],
            "rollback": rollback
        }
    )

def scenario_pvc_pending_missing_storageclass(world):
    ns = world["namespace"]
    w = world["workload"]

    world["inventory"]["storageclasses_present"] = []
    pvc_name = random.choice(["data", "uploads", "cache", "model-checkpoints"])
    w["volumes"].append({"name": pvc_name, "persistentVolumeClaim": {"claimName": pvc_name}})

    pod_name, getpods = render_kubectl_get_pods(world, "Pending", "0/1", 0, f"{random.randint(1,12)}m")

    events = render_events_table([
        {
            "last_seen": "18s",
            "type": "Warning",
            "reason": "FailedScheduling",
            "object": f"pod/{pod_name}",
            "message": "pod has unbound immediate PersistentVolumeClaims"
        },
        {
            "last_seen": "17s",
            "type": "Warning",
            "reason": "ProvisioningFailed",
            "object": f"persistentvolumeclaim/{pvc_name}",
            "message": 'storageclass.storage.k8s.io "standard" not found'
        }
    ])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Pending",
        container_state="Waiting",
        reason="Pending",
        restarts=0,
        events_table=events,
        last_state=None,
        exit_code=None,
        message="Unbound PVC",
        ready=False
    )

    logs = waiting_logs_error(world, pod_name, "ContainerCreating")

    diagnosis = f"Pod is Pending because PVC `{pvc_name}` is unbound; no valid StorageClass is available to provision storage."
    fix_plan = [
        "Ensure a valid StorageClass exists and the PVC references it correctly, or provision a matching PV.",
        f"Verify PVC `{pvc_name}` becomes Bound.",
        f"Confirm the Pod becomes Running and Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_get_storageclass", "cmd": "kubectl get storageclass"},
        {"type": "kubectl_get_pvc", "cmd": f"kubectl -n {ns} get pvc {pvc_name}"},
        {"type": "kubectl_patch_pvc", "cmd": f"kubectl -n {ns} patch pvc {pvc_name} -p '{{\"spec\":{{\"storageClassName\":\"standard\"}}}}'"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pvc && kubectl -n {ns} get pods"}
    ]

    rollback = ["Undo the PVC storageClass change if it points to the wrong class."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "pvc_pending_missing_storageclass",
            "variant": "unbound_pvc_missing_storageclass",
            "fault_params": {"pvc": pvc_name}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": 0, "oom_killed": False}
        },
        remediation={
            "category": "Pending",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": [f"PVC `{pvc_name}` Bound; pod Running and Ready in `{ns}`."],
            "rollback": rollback
        }
    )

def scenario_pvc_not_found_mountfail(world):
    ns = world["namespace"]
    w = world["workload"]

    pvc_name = rand_name("missing-pvc", 4)
    w["volumes"].append({"name": "data", "persistentVolumeClaim": {"claimName": pvc_name}})

    restarts = 0
    pod_name, getpods = render_kubectl_get_pods(world, "Pending", "0/1", restarts, f"{random.randint(1,12)}m")

    events = render_events_table([{
        "last_seen": "22s",
        "type": "Warning",
        "reason": "FailedMount",
        "object": f"pod/{pod_name}",
        "message": f'MountVolume.SetUp failed for volume "data" : persistentvolumeclaim "{pvc_name}" not found'
    }])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Pending",
        container_state="Waiting",
        reason="Pending",
        restarts=restarts,
        events_table=events,
        last_state=None,
        exit_code=None,
        message="PVC not found",
        ready=False
    )

    logs = waiting_logs_error(world, pod_name, "ContainerCreating")

    diagnosis = f"Pod cannot mount volume because PVC `{pvc_name}` does not exist in `{ns}` (FailedMount)."
    fix_plan = [
        f"Create PVC `{pvc_name}` or update the workload to use an existing PVC.",
        "Recreate or restart the workload after the claim reference is corrected.",
        f"Verify the Pod becomes Running and Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_get_pvc", "cmd": f"kubectl -n {ns} get pvc"},
        {"type": "kubectl_apply_pvc", "cmd": f"kubectl -n {ns} apply -f pvc.yaml  # name={pvc_name}"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pvc && kubectl -n {ns} describe pod {pod_name}"}
    ]

    rollback = ["Delete the wrongly created PVC and restore the correct claim reference if needed."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "pvc_not_found_mountfail",
            "variant": "pvc_missing",
            "fault_params": {"pvc": pvc_name}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": restarts, "oom_killed": False}
        },
        remediation={
            "category": "FailedMount",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": [f"No FailedMount remains; pod Running and Ready in `{ns}`."],
            "rollback": rollback
        }
    )

def scenario_readiness_probe_failure(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    restarts = 0
    pod_name, getpods = render_kubectl_get_pods(world, "Running", "0/1", restarts, f"{random.randint(2,10)}m")

    events = render_events_table([{
        "last_seen": "30s",
        "type": "Warning",
        "reason": "Unhealthy",
        "object": f"pod/{pod_name}",
        "message": "Readiness probe failed: HTTP probe failed with statuscode: 503"
    }])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Running",
        container_state="Running",
        reason="Running",
        restarts=restarts,
        events_table=events,
        last_state=None,
        exit_code=None,
        message=None,
        ready=False
    )

    logs = render_logs(base, [
        (0, f"INFO starting {w['name']}"),
        (5, "INFO http server started on :8080"),
        (12, "WARN dependency not ready: db connection refused"),
        (18, "WARN readiness returning 503"),
    ])
    logs = add_noise_logs(logs, base)

    diagnosis = "Pod is Running but NotReady because the readiness probe fails with HTTP 503."
    fix_plan = [
        "Confirm the readiness probe path, port, and timing match application startup behavior.",
        "Fix dependency readiness issues or tune probe delays and thresholds.",
        f"Verify the Pod becomes Ready in `{ns}` without unnecessary restarts."
    ]

    actions = [
        {"type": "kubectl_edit_workload", "cmd": f"kubectl -n {ns} edit {w['kind'].lower()}/{w['name']}"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods && kubectl -n {ns} describe pod {pod_name}"}
    ]

    rollback = ["Undo the readiness probe change if it hides real application readiness issues."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "readiness_probe_failure",
            "variant": "http_503",
            "fault_params": {}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": restarts, "oom_killed": False}
        },
        remediation={
            "category": "NotReady",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": ["READY becomes 1/1; endpoints include the pod."],
            "rollback": rollback
        }
    )

def scenario_liveness_probe_failure(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    restarts = random.randint(3, 15)
    pod_name, getpods = render_kubectl_get_pods(world, "CrashLoopBackOff", "0/1", restarts, f"{random.randint(3,12)}m")

    events = render_events_table([
        {
            "last_seen": "40s",
            "type": "Warning",
            "reason": "Unhealthy",
            "object": f"pod/{pod_name}",
            "message": 'Liveness probe failed: Get "http://10.0.0.12:8080/healthz": context deadline exceeded'
        },
        {
            "last_seen": "35s",
            "type": "Normal",
            "reason": "Killing",
            "object": f"pod/{pod_name}",
            "message": "Container failed liveness probe, will be restarted"
        }
    ])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Running",
        container_state="Waiting",
        reason="CrashLoopBackOff",
        restarts=restarts,
        events_table=events,
        last_state="Terminated",
        exit_code=1,
        message="Back-off restarting failed container",
        ready=False
    )

    logs = render_logs(base, [
        (0, f"INFO starting {w['name']}"),
        (10, "WARN health endpoint slow; liveness may timeout"),
    ])
    logs = add_noise_logs(logs, base)

    diagnosis = "Container is repeatedly restarted because the liveness probe fails before the application can respond successfully."
    fix_plan = [
        "Review the liveness probe path, port, timeout, and initial delay.",
        "Investigate application slowness, dependency latency, or resource pressure.",
        "Apply probe tuning and verify restarts stop increasing."
    ]

    actions = [
        {"type": "kubectl_edit_workload", "cmd": f"kubectl -n {ns} edit {w['kind'].lower()}/{w['name']}"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]

    rollback = ["Undo the liveness probe changes if they make failure detection too weak."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "liveness_probe_failure",
            "variant": "timeout",
            "fault_params": {}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": restarts, "oom_killed": False}
        },
        remediation={
            "category": "CrashLoopBackOff",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": ["Restarts stop increasing; pod Ready."],
            "rollback": rollback
        }
    )

def scenario_rbac_forbidden(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    restarts = random.randint(1, 6)
    pod_name, getpods = render_kubectl_get_pods(world, "CrashLoopBackOff", "0/1", restarts, f"{random.randint(2,10)}m")

    events = render_events_table([{
        "last_seen": "18s",
        "type": "Warning",
        "reason": "BackOff",
        "object": f"pod/{pod_name}",
        "message": "Back-off restarting failed container"
    }])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Running",
        container_state="Waiting",
        reason="CrashLoopBackOff",
        restarts=restarts,
        events_table=events,
        last_state="Terminated",
        exit_code=1,
        message="Back-off restarting failed container",
        ready=False
    )

    logs = render_logs(base, [
        (0, f"INFO starting {w['name']}"),
        (4, f'ERROR Forbidden: User "system:serviceaccount:{ns}:{w["serviceAccountName"]}" cannot list resource "pods" in API group "" in the namespace "{ns}"'),
    ])
    logs = add_noise_logs(logs, base)

    diagnosis = "Workload fails due to an RBAC permission error (Forbidden) for its ServiceAccount."
    fix_plan = [
        f"Check the permissions of ServiceAccount `{w['serviceAccountName']}`.",
        "Create or update the least-privilege Role and RoleBinding required by the app.",
        "Restart the workload and verify Forbidden errors disappear."
    ]

    actions = [
        {"type": "kubectl_auth_can_i", "cmd": f"kubectl auth can-i list pods --as=system:serviceaccount:{ns}:{w['serviceAccountName']} -n {ns}"},
        {"type": "kubectl_apply_rbac", "cmd": f"kubectl -n {ns} apply -f role.yaml -f rolebinding.yaml"},
        {"type": "kubectl_rollout_restart", "cmd": rollout_restart_cmd(ns, world)},
        {"type": "validate", "cmd": f"kubectl -n {ns} logs {w['kind'].lower()}/{w['name']} --tail=50"}
    ]

    rollback = ["Remove overly permissive RBAC and replace it with the correct least-privilege policy."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "rbac_forbidden",
            "variant": "serviceaccount_missing_role",
            "fault_params": {"serviceaccount": w["serviceAccountName"]}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": restarts, "oom_killed": False}
        },
        remediation={
            "category": "CrashLoopBackOff",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": ["Forbidden error disappears; pod stabilizes."],
            "rollback": rollback
        }
    )

def scenario_dns_resolution_failure(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    restarts = random.randint(1, 8)
    pod_name, getpods = render_kubectl_get_pods(world, "CrashLoopBackOff", "0/1", restarts, f"{random.randint(2,10)}m")

    events = render_events_table([{
        "last_seen": "22s",
        "type": "Warning",
        "reason": "BackOff",
        "object": f"pod/{pod_name}",
        "message": "Back-off restarting failed container"
    }])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Running",
        container_state="Waiting",
        reason="CrashLoopBackOff",
        restarts=restarts,
        events_table=events,
        last_state="Terminated",
        exit_code=1,
        message="Back-off restarting failed container",
        ready=False
    )

    dep = random.choice([
        "postgres.default.svc.cluster.local",
        "redis.default.svc.cluster.local",
        "kafka.observability.svc.cluster.local"
    ])

    logs = render_logs(base, [
        (0, f"INFO starting {w['name']}"),
        (3, f"INFO resolving dependency host {dep}"),
        (5, f"ERROR lookup {dep} on 10.96.0.10:53: no such host"),
        (6, "ERROR startup failed"),
    ])
    logs = add_noise_logs(logs, base)

    diagnosis = "Workload fails due to DNS resolution failure for a cluster service (no such host)."
    fix_plan = [
        "Confirm the Service name and namespace are correct.",
        "Check CoreDNS health and cluster DNS configuration.",
        "Fix the service reference or restore DNS, then verify the application resolves the hostname."
    ]

    actions = [
        {"type": "inspect_service", "cmd": f"kubectl -n {ns} get svc"},
        {"type": "inspect_coredns", "cmd": "kubectl -n kube-system get pods -l k8s-app=kube-dns"},
        {"type": "validate", "cmd": f"kubectl -n {ns} logs {w['kind'].lower()}/{w['name']} --tail=50"}
    ]

    rollback = ["Revert any DNS or service configuration change that worsens name resolution."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "dns_resolution_failure",
            "variant": "no_such_host",
            "fault_params": {"hostname": dep}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": restarts, "oom_killed": False}
        },
        remediation={
            "category": "CrashLoopBackOff",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": ["Hostname resolves; app connects; pod Ready."],
            "rollback": rollback
        }
    )

def scenario_service_connection_refused(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    restarts = random.randint(1, 8)
    pod_name, getpods = render_kubectl_get_pods(world, "CrashLoopBackOff", "0/1", restarts, f"{random.randint(2,10)}m")

    events = render_events_table([{
        "last_seen": "24s",
        "type": "Warning",
        "reason": "BackOff",
        "object": f"pod/{pod_name}",
        "message": "Back-off restarting failed container"
    }])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Running",
        container_state="Waiting",
        reason="CrashLoopBackOff",
        restarts=restarts,
        events_table=events,
        last_state="Terminated",
        exit_code=1,
        message="Back-off restarting failed container",
        ready=False
    )

    svc = random.choice(["redis", "postgres", "kafka"])
    if svc == "redis":
        host = f"redis.{ns}.svc.cluster.local:6379"
    elif svc == "postgres":
        host = f"postgres.{ns}.svc.cluster.local:5432"
    else:
        host = f"kafka.{ns}.svc.cluster.local:9092"

    logs = render_logs(base, [
        (0, f"INFO starting {w['name']}"),
        (6, f"ERROR dial tcp {host}: connect: connection refused")
    ])
    logs = add_noise_logs(logs, base)

    diagnosis = "App cannot connect to a dependency service because the connection is refused, usually due to the backend being down or the port being wrong."
    fix_plan = [
        f"Check whether Service `{svc}` has endpoints and whether its backing pods are Ready.",
        "Verify the configured port is correct and traffic is not blocked by NetworkPolicy.",
        "Restore the dependency or correct the service configuration, then verify the app connects."
    ]

    actions = [
        {"type": "check_service", "cmd": f"kubectl -n {ns} get svc {svc} && kubectl -n {ns} get endpoints {svc}"},
        {"type": "check_pods", "cmd": f"kubectl -n {ns} get pods"},
        {"type": "validate", "cmd": f"kubectl -n {ns} logs {w['kind'].lower()}/{w['name']} --tail=50"}
    ]

    rollback = ["Revert any incorrect service or port change."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "service_connection_refused",
            "variant": "dependency_down_or_port",
            "fault_params": {"service": svc}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": restarts, "oom_killed": False}
        },
        remediation={
            "category": "CrashLoopBackOff",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": ["Connection succeeds; pod Ready."],
            "rollback": rollback
        }
    )

def scenario_quota_exceeded_pods(world):
    ns = world["namespace"]
    w = world["workload"]

    quota = world["inventory"]["quota"]
    quota["pods_used"] = quota["pods_limit"]

    pod_name = f"{w['name']}-{rand_name('pod',4)}"
    getpods = "NAME\tREADY\tSTATUS\tRESTARTS\tAGE\n"

    events = render_events_table([{
        "last_seen": "14s",
        "type": "Warning",
        "reason": "FailedCreate",
        "object": f"replicaset/{w['name']}",
        "message": f'Error creating: pods "{pod_name}" is forbidden: exceeded quota: pods, requested: 1, used: {quota["pods_used"]}, limited: {quota["pods_limit"]}'
    }])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Pending",
        container_state="Waiting",
        reason="Pending",
        restarts=0,
        events_table=events,
        last_state=None,
        exit_code=None,
        message="Quota exceeded",
        ready=False
    )

    logs = "No logs available: pod was not successfully created.\n"

    diagnosis = "Controller cannot create a new Pod because the namespace ResourceQuota for pods is exhausted."
    fix_plan = [
        "Identify completed or unused pods that can be cleaned up, or increase the namespace pod quota.",
        "Retry the rollout after cleanup or quota adjustment.",
        f"Verify the workload can create Pods successfully in `{ns}`."
    ]

    actions = [
        {"type": "check_quota", "cmd": f"kubectl -n {ns} get resourcequota"},
        {"type": "cleanup", "cmd": f"kubectl -n {ns} get pods --field-selector=status.phase=Succeeded"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]

    rollback = ["Revert a quota increase if it causes broader resource pressure; prefer cleanup or rightsizing."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "quota_exceeded_pods",
            "variant": "resourcequota_pods",
            "fault_params": {"pods_used": quota["pods_used"], "pods_limit": quota["pods_limit"]}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": 0, "oom_killed": False}
        },
        remediation={
            "category": "FailedCreate",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": ["New pods are created; rollout proceeds."],
            "rollback": rollback
        }
    )
def scenario_oomkilled_limit_too_low(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    old_lim = w["container"]["resources"]["limits"]["memory"]
    w["container"]["resources"]["limits"]["memory"] = "256Mi"

    restarts = random.randint(2, 12)
    pod_name, getpods = render_kubectl_get_pods(world, "CrashLoopBackOff", "0/1", restarts, f"{random.randint(2,12)}m")

    events = render_events_table([
        {
            "last_seen": "25s",
            "type": "Warning",
            "reason": "Killing",
            "object": f"pod/{pod_name}",
            "message": f'Container {w["container"]["name"]} was OOMKilled (memory limit {w["container"]["resources"]["limits"]["memory"]})'
        },
        {
            "last_seen": "20s",
            "type": "Warning",
            "reason": "BackOff",
            "object": f"pod/{pod_name}",
            "message": "Back-off restarting failed container"
        }
    ])

    describe = render_describe_pod(
        world,
        pod_name,
        pod_phase="Running",
        container_state="Waiting",
        reason="CrashLoopBackOff",
        restarts=restarts,
        events_table=events,
        last_state="Terminated",
        exit_code=137,
        message="OOMKilled",
        ready=False
    )

    logs = render_logs(base, [
        (0, f"INFO starting {w['name']}"),
        (4, "INFO loading dataset into memory"),
        (9, "INFO processing batch size=50000"),
        (12, "Killed"),
    ])
    logs = add_noise_logs(logs, base)

    new_lim = choice_weighted([("512Mi", 40), ("1Gi", 45), ("2Gi", 15)])
    diagnosis = "Container is repeatedly OOMKilled because its memory limit is too low."
    fix_plan = [
        f"Increase the memory limit from `256Mi` to something more realistic such as `{new_lim}`.",
        "Adjust the memory request as needed to match application behavior.",
        f"Restart the workload and verify restarts stop increasing in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_patch_resources", "cmd": f"kubectl -n {ns} patch {w['kind'].lower()}/{w['name']} --type merge -p '<resources patch>'"},
        {"type": "kubectl_rollout_restart", "cmd": rollout_restart_cmd(ns, world)},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods && kubectl -n {ns} describe pod {pod_name}"}
    ]

    rollback = [f"Restore the previous memory limit `{old_lim}` if the new value is incorrect and investigate memory usage."]

    return pack_sample(
        world,
        fault={
            "scenario_id": "oomkilled_limit_too_low",
            "variant": "oomkilled",
            "fault_params": {"old_limit": old_lim, "new_limit": new_lim}
        },
        observations={
            "kubectl_get_pods": getpods,
            "kubectl_describe_pod": describe,
            "kubectl_get_events": events,
            "container_logs": logs,
            "metrics_snapshot": {"restarts": restarts, "oom_killed": True}
        },
        remediation={
            "category": "CrashLoopBackOff",
            "diagnosis": diagnosis,
            "fix_plan": fix_plan,
            "actions_structured": actions,
            "verification": ["Pod Ready; restarts stop increasing."],
            "rollback": rollback
        }
    )

# ---------------------------
# Scenario Registry (15–20 scenarios)
# ---------------------------

def scenario_registry():
    return [
        ("createcontainerconfigerror_missing_secret", scenario_crashloop_missing_secret),
        ("createcontainerconfigerror_bad_configmap_key", scenario_crashloop_bad_configmap_key),
        ("crashloop_bad_args", scenario_crashloop_bad_args),

        ("imagepull_bad_tag", scenario_imagepull_bad_tag),
        ("imagepull_registry_auth", scenario_imagepull_registry_auth),

        ("failedscheduling_taint", scenario_failedscheduling_taint),
        ("failedscheduling_insufficient_memory", scenario_failedscheduling_insufficient_memory),
        ("failedscheduling_insufficient_cpu", scenario_failedscheduling_insufficient_cpu),
        ("failedscheduling_nodeselector_mismatch", scenario_nodeselector_mismatch),

        ("pending_unbound_pvc_missing_storageclass", scenario_pvc_pending_missing_storageclass),
        ("failedmount_pvc_not_found", scenario_pvc_not_found_mountfail),

        ("crashloop_oomkilled_limit_too_low", scenario_oomkilled_limit_too_low),

        ("notready_readiness_probe_failure", scenario_readiness_probe_failure),
        ("crashloop_liveness_probe_failure", scenario_liveness_probe_failure),

        ("crashloop_rbac_forbidden", scenario_rbac_forbidden),
        ("crashloop_dns_resolution_failure", scenario_dns_resolution_failure),
        ("crashloop_service_connection_refused", scenario_service_connection_refused),
        ("failedcreate_quota_exceeded_pods", scenario_quota_exceeded_pods),
    ]


# ---------------------------
# BALANCED dataset generation
# ---------------------------

def generate_balanced_dataset(per_scenario_targets, out_source, out_stats, max_tries_per_sample=25):
    scenarios = dict(scenario_registry())
    counts = {sid: 0 for sid in per_scenario_targets}
    source_rows = []
    rejected = 0

    for sid, target in per_scenario_targets.items():
        fn = scenarios[sid]
        for _ in range(target):
            ok = False
            for _try in range(max_tries_per_sample):
                world = build_world()
                sample = fn(world)
                valid, _reason = validate_sample(sample)
                if valid:
                    ok = True
                    break
            if not ok:
                rejected += 1
                continue

            counts[sid] += 1
            source_rows.append(sample)

    with open(out_source, "w") as f:
        for r in source_rows:
            f.write(json.dumps(r) + "\n")

    stats = {
        "generated_total": len(source_rows),
        "rejected_samples": rejected,
        "per_scenario_targets": per_scenario_targets,
        "per_scenario_generated": counts
    }
    with open(out_stats, "w") as f:
        json.dump(stats, f, indent=2)

    return stats


# ---------------------------
# CLI
# ---------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", type=str, default=".", help="Output directory")
    ap.add_argument("--seed", type=int, default=None, help="Random seed")
    ap.add_argument("--per_scenario", type=int, default=None, help="Fixed count per scenario (e.g., 500)")
    ap.add_argument("--per_scenario_min", type=int, default=400, help="Min count per scenario")
    ap.add_argument("--per_scenario_max", type=int, default=600, help="Max count per scenario")
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    os.makedirs(args.outdir, exist_ok=True)
    out_source = os.path.join(args.outdir, "synthetic_source.jsonl")
    out_stats = os.path.join(args.outdir, "stats.json")

    reg = scenario_registry()
    scenario_ids = [sid for sid, _ in reg]

    per_scenario_targets = {}
    if args.per_scenario is not None:
        for sid in scenario_ids:
            per_scenario_targets[sid] = args.per_scenario
    else:
        lo = args.per_scenario_min
        hi = args.per_scenario_max
        if lo > hi:
            raise ValueError("--per_scenario_min must be <= --per_scenario_max")
        for sid in scenario_ids:
            per_scenario_targets[sid] = random.randint(lo, hi)

    total_target = sum(per_scenario_targets.values())
    print(f"[plan] scenarios={len(scenario_ids)} total_target={total_target} per_scenario≈{min(per_scenario_targets.values())}-{max(per_scenario_targets.values())}")

    stats = generate_balanced_dataset(per_scenario_targets, out_source, out_stats)
    print(json.dumps({
        "generated_total": stats["generated_total"],
        "out_source": out_source,
        "out_stats": out_stats,
        "per_scenario_generated": stats["per_scenario_generated"],
    }, indent=2))


if __name__ == "__main__":
    main()