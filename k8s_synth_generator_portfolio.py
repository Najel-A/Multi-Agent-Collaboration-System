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

def render_describe_pod(world, pod_name, reason, restarts, events_table, last_state="Terminated", exit_code=1, message=None):
    ns = world["namespace"]
    w = world["workload"]
    node = random.choice(world["nodes"])["name"]
    start = now_utc() - timedelta(minutes=random.randint(2, 15))
    msg = message or "Back-off restarting failed container"
    return f"""Name:           {pod_name}
Namespace:      {ns}
Priority:       0
Node:           {node}
Start Time:     {ts(start)}
Labels:         app={w['name']}
Status:         Running
Containers:
  {w['container']['name']}:
    Image:      {w['container']['image']}:{w['container']['tag']}
    State:      Waiting
      Reason:   {reason}
      Message:  {msg}
    Last State: {last_state}
      Exit Code: {exit_code}
    Ready:      False
    Restart Count: {restarts}
Events:
{events_table}
"""

def render_logs(base: datetime, lines_with_offsets):
    lines = []
    for off, msg in sorted(lines_with_offsets, key=lambda x: x[0]):
        lines.append(f"{ts(base + timedelta(seconds=off))} {msg}")
    return "\n".join(lines).rstrip() + "\n"

def add_noise_logs(log_txt, base_dt):
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


# ---------------------------
# Validators (lightweight but important)
# ---------------------------

def validate_sample(sample):
    obs = sample.get("observations", {})
    rem = sample.get("remediation", {})
    _ctx = sample.get("context", {})

    if not obs.get("kubectl_get_pods") or not obs.get("kubectl_describe_pod"):
        return False, "missing core signals"

    # evidence rule: diagnosis keyword should appear in signals
    diagnosis = (rem.get("diagnosis") or "").lower()
    signals_text = (obs.get("kubectl_get_events","") + obs.get("container_logs","") + obs.get("kubectl_describe_pod","")).lower()
    for kw in ["secret", "configmap", "manifest", "unauthorized", "taint", "toleration", "insufficient", "oom", "storageclass", "pvc", "forbidden", "dns", "probe", "quota", "connection refused"]:
        if kw in diagnosis and kw not in signals_text:
            return False, f"evidence missing for '{kw}'"

    return True, None





# ---------------------------
# Scenario Implementations (NO GITOPS; keep scenarios otherwise unchanged)
# ---------------------------

def scenario_crashloop_missing_secret(world):
    """
    SRE-style: confirm failure mode via events/describe, identify exact reference (env var -> secretKeyRef),
    verify secret absence, suggest least-risk fix (create secret or point to correct one), then rollout.
    """
    ns = world["namespace"]
    w = world["workload"]
    inv = world["inventory"]
    base = now_utc()

    # --- pick a secret that is NOT present to ensure scenario is valid ---
    existing = set(inv["secrets_present"])
    pool = ["db-credentials", "payments-db", "api-secrets", "redis-auth", "kafka-sasl", "s3-keys"]
    missing_secret = random.choice([s for s in pool if s not in existing] or ["payments-db"])
    missing_key = random.choice(["TOKEN", "DB_PASSWORD", "PASSWORD", "SASL_PASSWORD"])

    # Workload references a missing secret (the root cause)
    w["env"].append({
        "name": missing_key,
        "valueFrom": {"secretKeyRef": {"name": missing_secret, "key": missing_key}}
    })

    restarts = random.randint(0, 2)  # CreateContainerConfigError often happens before lots of restarts
    pod_name, getpods = render_kubectl_get_pods(
        world,
        status="CreateContainerConfigError",
        ready="0/1",
        restarts=restarts,
        age=f"{random.randint(1,9)}m"
    )

    # Events and describe: closer to what kubernetes usually emits
    events_rows = [
        {
            "last_seen": "22s",
            "type": "Warning",
            "reason": "Failed",
            "object": f"pod/{pod_name}",
            "message": f'Error: secret "{missing_secret}" not found'
        },
        {
            "last_seen": "22s",
            "type": "Warning",
            "reason": "Failed",
            "object": f"pod/{pod_name}",
            "message": f'Error: couldn\'t find key "{missing_key}" in Secret "{missing_secret}"'
            # NOTE: This line is realistic but technically only happens if secret exists; keep it optional/noisy
            # You can remove this row if you want strict correctness for "secret not found".
        } if maybe(0.15) else None,
    ]
    events_rows = [r for r in events_rows if r is not None]
    events = render_events_table(events_rows)

    # Describe pod includes the "Waiting" reason + message
    describe = render_describe_pod(
        world,
        pod_name=pod_name,
        reason="CreateContainerConfigError",
        restarts=restarts,
        events_table=events,
        message=f'Error: secret "{missing_secret}" not found'
    )

    # Logs: in this failure mode, the container often never starts, so logs may be empty/unavailable.
    # We model that realistically by sometimes returning a kubectl logs error.
    if maybe(0.65):
        logs = (
            f'Error from server (BadRequest): container "{w["container"]["name"]}" in pod "{pod_name}" is waiting to start: '
            "CreateContainerConfigError\n"
        )
    else:
        logs = render_logs(base, [
            (0, f"INFO starting {w['name']} version={w['container']['tag']}"),
            (1, "INFO reading env"),
            (2, f"ERROR required env var {missing_key} not set"),
        ])
        logs = add_noise_logs(logs, base)

    # --- SRE diagnosis: precise + references evidence ---
    diagnosis = (
        f"Pod is stuck in CreateContainerConfigError because the workload references Secret "
        f"`{missing_secret}` (key `{missing_key}`) that does not exist in namespace `{ns}` "
        f"(see events/describe: secret not found)."
    )

    # --- SRE plan: verify -> fix -> rollout -> verify, plus least-privilege concerns ---
    fix_plan = [
        f"Confirm the reference: inspect the workload spec for env/volumes pointing to Secret `{missing_secret}`.",
        f"Check whether Secret `{missing_secret}` exists in `{ns}` (and whether a similarly-named secret exists that the workload should use).",
        f"If the secret should exist: create/restore Secret `{missing_secret}` with key `{missing_key}` using the approved secret source (Vault/ExternalSecrets/SealedSecrets/etc.).",
        f"If the reference is wrong: update the workload to point to the correct existing Secret+key instead of creating a new one.",
        "Trigger a rollout only after the secret/reference is corrected.",
        "Verify the pod becomes Ready and events stop reporting secret-not-found."
    ]

    # Structured actions: what an agent could do (still synthetic, but SRE-sequenced)
    # Avoid always using rollout restart deploy/... because workload kind may not be Deployment.
    workload_ref = f"{w['kind'].lower()}/{w['name']}"
    restart_cmd = (
        f"kubectl -n {ns} rollout restart deploy/{w['name']}"
        if w["kind"] == "Deployment"
        else f"kubectl -n {ns} rollout restart {workload_ref}"
    )

    actions = [
        {"type": "kubectl_get_pods", "cmd": f"kubectl -n {ns} get pods"},
        {"type": "kubectl_describe_pod", "cmd": f"kubectl -n {ns} describe pod {pod_name}"},
        {"type": "kubectl_get_events", "cmd": f"kubectl -n {ns} get events --sort-by=.lastTimestamp"},
        {"type": "kubectl_get_workload", "cmd": f"kubectl -n {ns} get {workload_ref} -o yaml"},
        {"type": "kubectl_check_secret", "cmd": f"kubectl -n {ns} get secret {missing_secret}"},
        {
            "type": "kubectl_create_secret",
            "cmd": f"kubectl -n {ns} create secret generic {missing_secret} --from-literal={missing_key}=REDACTED",
            "guardrails": [
                "Prefer ExternalSecrets/SealedSecrets/Vault; do not paste real secrets into shells in prod.",
                "Validate key name matches exactly."
            ]
        },
        {"type": "kubectl_rollout_restart", "cmd": restart_cmd},
        {"type": "kubectl_rollout_status", "cmd": f"kubectl -n {ns} rollout status {workload_ref} --timeout=2m"},
        {"type": "kubectl_verify_ready", "cmd": f"kubectl -n {ns} get pods -l app={w['name']}"},
    ]

    verification = [
        f"`kubectl -n {ns} get pods` shows the pod(s) Ready (1/1).",
        f"`kubectl -n {ns} get events` no longer shows secret-not-found for `{missing_secret}`.",
        "Application health endpoints / SLO signals are stable after rollout."
    ]

    rollback = [
        "If creating the secret was incorrect: delete the newly-created Secret and restore the correct one from the approved source of truth.",
        "If the workload update caused issues: roll back to the last known-good revision (e.g., rollout undo / revert GitOps change)."
    ]

    return pack_sample(
        world,
        fault={
            "scenario_id": "crashloop_missing_secret",
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
    base = now_utc()

    cm = random.choice(inv["configmaps_present"])
    bad_key = random.choice(["CONFIG_PATH", "MODE", "FEATURE_X", "LOG_LEVEL"])
    w["env"].append({"name": bad_key, "valueFrom": {"configMapKeyRef": {"name": cm, "key": bad_key}}})

    restarts = random.randint(2, 10)
    pod_name, getpods = render_kubectl_get_pods(world, "CreateContainerConfigError", "0/1", restarts, f"{random.randint(1,9)}m")
    events = render_events_table([{
        "last_seen": "12s", "type": "Warning", "reason": "Failed", "object": f"pod/{pod_name}",
        "message": f"configmap \"{cm}\" does not contain key \"{bad_key}\""
    }])
    describe = render_describe_pod(world, pod_name, "CreateContainerConfigError", restarts, events, message="Error: configmap key missing")
    logs = render_logs(base, [
        (0, f"INFO starting {w['name']} version={w['container']['tag']}"),
        (3, f"ERROR required config key missing: {bad_key} (configmap={cm})"),
    ])
    logs = add_noise_logs(logs, base)

    diagnosis = f"Pod cannot start because ConfigMap `{cm}` is missing key `{bad_key}`."
    fix_plan = [
        f"Add key `{bad_key}` to ConfigMap `{cm}` or update workload to use an existing key.",
        "Restart/rollout workload.",
        f"Verify pod becomes Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_edit_configmap", "cmd": f"kubectl -n {ns} edit configmap {cm}  # add key {bad_key}"},
        {"type": "kubectl_rollout_restart", "cmd": f"kubectl -n {ns} rollout restart deploy/{w['name']}"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]
    rollback = ["Undo configmap changes; re-apply correct data."]

    return pack_sample(
        world,
        fault={"scenario_id": "crashloop_bad_configmap_key", "variant": "configmap_key_missing", "fault_params": {"configmap": cm, "missing_key": bad_key}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": restarts, "oom_killed": False}},
        remediation={"category": "CreateContainerConfigError", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": [f"Pod Ready in `{ns}`; no configmap-key warnings."],
                     "rollback": rollback}
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
        "last_seen": "20s", "type": "Warning", "reason": "BackOff", "object": f"pod/{pod_name}",
        "message": "Back-off restarting failed container"
    }])
    describe = render_describe_pod(world, pod_name, "CrashLoopBackOff", restarts, events, exit_code=2, message="Invalid arguments")
    logs = render_logs(base, [
        (0, f"INFO starting {w['name']}"),
        (3, f"ERROR invalid argument: {bad_flag}"),
        (4, "ERROR exiting with code 2"),
    ])
    logs = add_noise_logs(logs, base)

    diagnosis = "Container crashes due to invalid command-line arguments / flags."
    fix_plan = [
        "Correct the container args/command to valid values in the workload spec.",
        "Apply change and rollout restart.",
        f"Verify pod stabilizes and becomes Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_edit_deploy", "cmd": f"kubectl -n {ns} edit deploy/{w['name']}  # fix args/command"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]
    rollback = ["Undo args edit if it breaks startup."]

    return pack_sample(
        world,
        fault={"scenario_id": "crashloop_bad_args", "variant": "invalid_args", "fault_params": {"bad_arg": bad_flag}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": restarts, "oom_killed": False}},
        remediation={"category": "CrashLoopBackOff", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": ["Restarts stop increasing; pod Ready."],
                     "rollback": rollback}
    )

def scenario_imagepull_bad_tag(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    good_tag = w["container"]["tag"]
    bad_tag = good_tag + "-typo"
    w["container"]["tag"] = bad_tag

    pod_name, getpods = render_kubectl_get_pods(world, "ImagePullBackOff", "0/1", 0, f"{random.randint(1,6)}m")
    events = render_events_table([
        {"last_seen": "20s", "type": "Warning", "reason": "Failed", "object": f"pod/{pod_name}",
         "message": f"Failed to pull image \"{w['container']['image']}:{bad_tag}\": manifest unknown"},
        {"last_seen": "15s", "type": "Warning", "reason": "BackOff", "object": f"pod/{pod_name}",
         "message": f"Back-off pulling image \"{w['container']['image']}:{bad_tag}\""}
    ])
    describe = render_describe_pod(world, pod_name, "ImagePullBackOff", 0, events, last_state="Waiting", exit_code=0, message="Back-off pulling image")
    logs = render_logs(base, [(0, "INFO container not started; image pull failed")])

    diagnosis = "Image pull fails because the image tag does not exist (manifest unknown)."
    fix_plan = [
        f"Update image tag from `{bad_tag}` to a valid tag (e.g., `{good_tag}`) in the workload spec.",
        "Apply change and wait for new pod to pull the correct image.",
        f"Verify pod becomes Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_set_image", "cmd": f"kubectl -n {ns} set image deploy/{w['name']} {w['container']['name']}={w['container']['image']}:{good_tag}"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]
    rollback = ["Set image back to prior tag and validate."]

    return pack_sample(
        world,
        fault={"scenario_id": "imagepull_bad_tag", "variant": "manifest_unknown", "fault_params": {"bad_tag": bad_tag, "good_tag": good_tag}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": 0, "oom_killed": False}},
        remediation={"category": "ImagePullBackOff", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": [f"Pod Ready in `{ns}`; image pulled successfully."],
                     "rollback": rollback}
    )

def scenario_imagepull_registry_auth(world):
    ns = world["namespace"]
    w = world["workload"]
    inv = world["inventory"]
    base = now_utc()

    w["container"]["image"] = choice_weighted([("ghcr.io/acme/private-api", 60), ("docker.io/acme/private-worker", 40)])
    needed_secret = "registry-creds"
    inv["secrets_present"] = [s for s in inv["secrets_present"] if s != needed_secret]

    pod_name, getpods = render_kubectl_get_pods(world, "ImagePullBackOff", "0/1", 0, f"{random.randint(1,6)}m")
    events = render_events_table([{
        "last_seen": "25s", "type": "Warning", "reason": "Failed", "object": f"pod/{pod_name}",
        "message": f"Failed to pull image \"{w['container']['image']}:{w['container']['tag']}\": unauthorized: authentication required"
    }])
    describe = render_describe_pod(world, pod_name, "ImagePullBackOff", 0, events, last_state="Waiting", exit_code=0, message="unauthorized: authentication required")
    logs = render_logs(base, [(0, "INFO container not started; registry auth failed")])

    diagnosis = "Image pull fails due to missing/invalid registry credentials (unauthorized)."
    fix_plan = [
        f"Create imagePullSecret `{needed_secret}` in `{ns}` and reference it from the ServiceAccount or Pod spec.",
        "Apply change and restart/rollout if needed.",
        "Verify image pull succeeds and pod becomes Ready."
    ]

    actions = [
        {"type": "kubectl_create_registry_secret", "cmd": f"kubectl -n {ns} create secret docker-registry {needed_secret} --docker-server=... --docker-username=... --docker-password=REDACTED"},
        {"type": "kubectl_patch_sa", "cmd": f"kubectl -n {ns} patch serviceaccount {w['serviceAccountName']} -p '{{\"imagePullSecrets\":[{{\"name\":\"{needed_secret}\"}}]}}'"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]
    rollback = ["Remove imagePullSecret reference or delete secret."]

    return pack_sample(
        world,
        fault={"scenario_id": "imagepull_registry_auth", "variant": "unauthorized", "fault_params": {"secret_needed": needed_secret}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": 0, "oom_killed": False}},
        remediation={"category": "ImagePullBackOff", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": [f"Pod Ready in `{ns}`; no unauthorized image pull errors."],
                     "rollback": rollback}
    )

def scenario_failedscheduling_taint(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    taint_key = "dedicated"
    taint_val = random.choice(["infra", "gpu", "batch"])
    for node in world["nodes"]:
        if maybe(0.7):
            node["taints"].append({"key": taint_key, "value": taint_val, "effect": "NoSchedule"})
    w["tolerations"] = []

    pod_name, getpods = render_kubectl_get_pods(world, "Pending", "0/1", 0, f"{random.randint(1,10)}m")
    events = render_events_table([{
        "last_seen": "8s", "type": "Warning", "reason": "FailedScheduling", "object": f"pod/{pod_name}",
        "message": f"0/{len(world['nodes'])} nodes are available: {len(world['nodes'])} node(s) had untolerated taint {{{taint_key}={taint_val}: NoSchedule}}."
    }])
    describe = render_describe_pod(world, pod_name, "Pending", 0, events, last_state="Waiting", exit_code=0, message="FailedScheduling")
    logs = render_logs(base, [(0, "INFO pod not scheduled; no container logs")])

    diagnosis = f"Pod cannot schedule due to untolerated taint `{taint_key}={taint_val}:NoSchedule`."
    fix_plan = [
        f"Add toleration for taint `{taint_key}={taint_val}:NoSchedule` OR adjust taints/nodeSelector strategy.",
        "Apply change and verify pod schedules.",
        f"Confirm pod becomes Running/Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_patch_deploy", "cmd": f"kubectl -n {ns} patch deploy/{w['name']} --type merge -p '<tolerations patch>'"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]
    rollback = ["Remove toleration patch and redeploy."]

    return pack_sample(
        world,
        fault={"scenario_id": "failedscheduling_taint", "variant": "untolerated_taint", "fault_params": {"taint_key": taint_key, "taint_value": taint_val}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": 0, "oom_killed": False}},
        remediation={"category": "FailedScheduling", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": [f"Pod scheduled and Running in `{ns}`."],
                     "rollback": rollback}
    )

def scenario_failedscheduling_insufficient_memory(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    w["container"]["resources"]["requests"]["memory"] = "64Gi"
    pod_name, getpods = render_kubectl_get_pods(world, "Pending", "0/1", 0, f"{random.randint(1,10)}m")
    events = render_events_table([{
        "last_seen": "9s", "type": "Warning", "reason": "FailedScheduling", "object": f"pod/{pod_name}",
        "message": f"0/{len(world['nodes'])} nodes are available: {len(world['nodes'])} Insufficient memory."
    }])
    describe = render_describe_pod(world, pod_name, "Pending", 0, events, last_state="Waiting", exit_code=0, message="Insufficient memory")
    logs = render_logs(base, [(0, "INFO pod not scheduled; insufficient node resources")])

    diagnosis = "Pod cannot schedule because its memory request exceeds available node capacity (Insufficient memory)."
    new_req = choice_weighted([("256Mi", 15), ("512Mi", 35), ("1Gi", 35), ("2Gi", 15)])
    fix_plan = [
        f"Reduce memory request to a realistic value (e.g., `{new_req}`) OR add larger nodes.",
        "Apply change and verify scheduling succeeds.",
        f"Confirm pod becomes Running/Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_patch_resources", "cmd": f"kubectl -n {ns} patch deploy/{w['name']} --type merge -p '<resources patch>'"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]
    rollback = ["Restore previous resource values."]

    return pack_sample(
        world,
        fault={"scenario_id": "failedscheduling_insufficient_memory", "variant": "insufficient_memory", "fault_params": {"requested_memory": "64Gi", "suggested_memory": new_req}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": 0, "oom_killed": False}},
        remediation={"category": "FailedScheduling", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": [f"Pod scheduled and Running in `{ns}`."],
                     "rollback": rollback}
    )

def scenario_failedscheduling_insufficient_cpu(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    w["container"]["resources"]["requests"]["cpu"] = "32"
    pod_name, getpods = render_kubectl_get_pods(world, "Pending", "0/1", 0, f"{random.randint(1,10)}m")
    events = render_events_table([{
        "last_seen": "11s", "type": "Warning", "reason": "FailedScheduling", "object": f"pod/{pod_name}",
        "message": f"0/{len(world['nodes'])} nodes are available: {len(world['nodes'])} Insufficient cpu."
    }])
    describe = render_describe_pod(world, pod_name, "Pending", 0, events, last_state="Waiting", exit_code=0, message="Insufficient cpu")
    logs = render_logs(base, [(0, "INFO pod not scheduled; insufficient CPU")])

    diagnosis = "Pod cannot schedule because its CPU request exceeds available node capacity (Insufficient cpu)."
    new_req = choice_weighted([("200m", 20), ("500m", 40), ("1", 30), ("2", 10)])
    fix_plan = [
        f"Reduce CPU request to a realistic value (e.g., `{new_req}`) OR add nodes with more CPU.",
        "Apply change and verify scheduling succeeds.",
        f"Confirm pod becomes Running/Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_patch_resources", "cmd": f"kubectl -n {ns} patch deploy/{w['name']} --type merge -p '<resources patch>'"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]
    rollback = ["Restore previous resource values."]

    return pack_sample(
        world,
        fault={"scenario_id": "failedscheduling_insufficient_cpu", "variant": "insufficient_cpu", "fault_params": {"requested_cpu": "32", "suggested_cpu": new_req}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": 0, "oom_killed": False}},
        remediation={"category": "FailedScheduling", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": [f"Pod scheduled and Running in `{ns}`."],
                     "rollback": rollback}
    )

def scenario_nodeselector_mismatch(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    # force selector that no node has
    w["nodeSelector"] = {"kubernetes.io/arch": "riscv64"}

    pod_name, getpods = render_kubectl_get_pods(world, "Pending", "0/1", 0, f"{random.randint(1,10)}m")
    events = render_events_table([{
        "last_seen": "9s", "type": "Warning", "reason": "FailedScheduling", "object": f"pod/{pod_name}",
        "message": f"0/{len(world['nodes'])} nodes are available: {len(world['nodes'])} node(s) didn't match Pod's node affinity/selector."
    }])
    describe = render_describe_pod(world, pod_name, "Pending", 0, events, last_state="Waiting", exit_code=0, message="nodeSelector mismatch")
    logs = render_logs(base, [(0, "INFO pod not scheduled; nodeSelector mismatch")])

    diagnosis = "Pod cannot schedule because nodeSelector/affinity does not match any nodes."
    fix_plan = [
        "Remove or correct nodeSelector/affinity so it matches real node labels.",
        "Apply change and verify pod schedules.",
        f"Confirm pod becomes Running/Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_edit_deploy", "cmd": f"kubectl -n {ns} edit deploy/{w['name']}  # fix nodeSelector"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]
    rollback = ["Undo selector edit if it was needed for placement."]

    return pack_sample(
        world,
        fault={"scenario_id": "nodeselector_mismatch", "variant": "no_matching_nodes", "fault_params": {"nodeSelector": w["nodeSelector"]}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": 0, "oom_killed": False}},
        remediation={"category": "FailedScheduling", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": [f"Pod scheduled and Running in `{ns}`."],
                     "rollback": rollback}
    )

def scenario_pvc_pending_missing_storageclass(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    world["inventory"]["storageclasses_present"] = []
    pvc_name = random.choice(["data", "uploads", "cache", "model-checkpoints"])
    w["volumes"].append({"name": pvc_name, "persistentVolumeClaim": {"claimName": pvc_name}})

    pod_name, getpods = render_kubectl_get_pods(world, "Pending", "0/1", 0, f"{random.randint(1,12)}m")
    events = render_events_table([{
        "last_seen": "18s", "type": "Warning", "reason": "FailedScheduling", "object": f"pod/{pod_name}",
        "message": "pod has unbound immediate PersistentVolumeClaims"
    }])
    describe = render_describe_pod(world, pod_name, "Pending", 0, events, last_state="Waiting", exit_code=0, message="Unbound PVC")
    logs = render_logs(base, [(0, "INFO pod waiting for PVC binding")])

    diagnosis = "Pod is Pending because PVC is unbound (missing/invalid StorageClass or no PV available)."
    fix_plan = [
        "Ensure a valid StorageClass exists (e.g., `standard`) and PVC references it, or provision a suitable PV.",
        "Apply changes and verify PVC becomes Bound.",
        f"Confirm pod becomes Running/Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_patch_pvc", "cmd": f"kubectl -n {ns} patch pvc {pvc_name} -p '{{\"spec\":{{\"storageClassName\":\"standard\"}}}}'"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pvc && kubectl -n {ns} get pods"}
    ]
    rollback = ["Undo PVC patch if incorrect."]

    return pack_sample(
        world,
        fault={"scenario_id": "pvc_pending_missing_storageclass", "variant": "unbound_pvc", "fault_params": {"pvc": pvc_name}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": 0, "oom_killed": False}},
        remediation={"category": "Pending", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": [f"PVC Bound; pod Running/Ready in `{ns}`."],
                     "rollback": rollback}
    )

def scenario_pvc_not_found_mountfail(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    pvc_name = rand_name("missing-pvc", 4)
    w["volumes"].append({"name": "data", "persistentVolumeClaim": {"claimName": pvc_name}})

    restarts = random.randint(0, 3)
    pod_name, getpods = render_kubectl_get_pods(world, "Pending", "0/1", restarts, f"{random.randint(1,12)}m")
    events = render_events_table([{
        "last_seen": "22s", "type": "Warning", "reason": "FailedMount", "object": f"pod/{pod_name}",
        "message": f"MountVolume.SetUp failed for volume \"data\" : persistentvolumeclaim \"{pvc_name}\" not found"
    }])
    describe = render_describe_pod(world, pod_name, "Pending", restarts, events, last_state="Waiting", exit_code=0, message="PVC not found")
    logs = render_logs(base, [(0, "INFO pod waiting for volume mount")])

    diagnosis = f"Pod cannot mount volume because PVC `{pvc_name}` does not exist in `{ns}` (FailedMount)."
    fix_plan = [
        f"Create PVC `{pvc_name}` OR update workload volume claimName to an existing PVC.",
        "Apply change and verify mount succeeds.",
        f"Confirm pod becomes Running/Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_apply_pvc", "cmd": f"kubectl -n {ns} apply -f pvc.yaml  # name={pvc_name}"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pvc && kubectl -n {ns} describe pod {pod_name}"}
    ]
    rollback = ["Delete created PVC if incorrect; re-apply correct PVC."]

    return pack_sample(
        world,
        fault={"scenario_id": "pvc_not_found_mountfail", "variant": "pvc_missing", "fault_params": {"pvc": pvc_name}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": restarts, "oom_killed": False}},
        remediation={"category": "FailedMount", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": [f"No FailedMount; pod Running/Ready in `{ns}`."],
                     "rollback": rollback}
    )

def scenario_oomkilled_limit_too_low(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    w["container"]["resources"]["limits"]["memory"] = "256Mi"
    restarts = random.randint(2, 12)
    pod_name, getpods = render_kubectl_get_pods(world, "CrashLoopBackOff", "0/1", restarts, f"{random.randint(2,12)}m")
    events = render_events_table([
        {"last_seen": "25s", "type": "Warning", "reason": "OOMKilled", "object": f"pod/{pod_name}",
         "message": f"Container {w['container']['name']} was OOMKilled (memory limit {w['container']['resources']['limits']['memory']})"},
        {"last_seen": "20s", "type": "Warning", "reason": "BackOff", "object": f"pod/{pod_name}",
         "message": "Back-off restarting failed container"}
    ])
    describe = render_describe_pod(world, pod_name, "CrashLoopBackOff", restarts, events, exit_code=137, message="OOMKilled")
    logs = render_logs(base, [(0, f"INFO starting {w['name']}"), (6, "ERROR process killed (likely OOM)")])
    logs = add_noise_logs(logs, base)

    diagnosis = "Container is repeatedly OOMKilled because memory limit is too low."
    old_lim = w["container"]["resources"]["limits"]["memory"]
    new_lim = choice_weighted([("512Mi", 40), ("1Gi", 45), ("2Gi", 15)])
    fix_plan = [
        f"Increase memory limit from `{old_lim}` to `{new_lim}` (and adjust request if needed).",
        "Apply change and rollout restart.",
        f"Verify restarts stop increasing and pod becomes Ready in `{ns}`."
    ]

    actions = [
        {"type": "kubectl_patch_resources", "cmd": f"kubectl -n {ns} patch deploy/{w['name']} --type merge -p '<resources patch>'"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods && kubectl -n {ns} describe pod {pod_name}"}
    ]
    rollback = ["Restore prior limit and investigate memory usage."]

    return pack_sample(
        world,
        fault={"scenario_id": "oomkilled_limit_too_low", "variant": "oomkilled", "fault_params": {"old_limit": old_lim, "new_limit": new_lim}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": restarts, "oom_killed": True}},
        remediation={"category": "CrashLoopBackOff", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": ["Pod Ready; restarts stop increasing."],
                     "rollback": rollback}
    )

def scenario_readiness_probe_failure(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    restarts = random.randint(0, 3)
    pod_name, getpods = render_kubectl_get_pods(world, "Running", "0/1", restarts, f"{random.randint(2,10)}m")
    events = render_events_table([{
        "last_seen": "30s", "type": "Warning", "reason": "Unhealthy", "object": f"pod/{pod_name}",
        "message": "Readiness probe failed: HTTP probe failed with statuscode: 503"
    }])
    describe = render_describe_pod(world, pod_name, "Running", restarts, events, last_state="Running", exit_code=0, message="Readiness probe failing")
    logs = render_logs(base, [
        (0, f"INFO starting {w['name']}"),
        (5, "INFO http server started on :8080"),
        (12, "WARN dependency not ready: db connection refused"),
        (18, "WARN readiness returning 503"),
    ])
    logs = add_noise_logs(logs, base)

    diagnosis = "Pod is running but NotReady due to failing readiness probe (503)."
    fix_plan = [
        "Confirm readiness probe path/port/initialDelay match app behavior.",
        "Fix dependency connectivity (DB/cache) or adjust probe timeouts.",
        "Apply changes and verify pod becomes Ready."
    ]

    actions = [
        {"type": "kubectl_edit_deploy", "cmd": f"kubectl -n {ns} edit deploy/{w['name']}  # fix readinessProbe"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods && kubectl -n {ns} describe pod {pod_name}"}
    ]
    rollback = ["Undo probe edit and redeploy."]

    return pack_sample(
        world,
        fault={"scenario_id": "readiness_probe_failure", "variant": "http_503", "fault_params": {}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": restarts, "oom_killed": False}},
        remediation={"category": "NotReady", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": ["READY becomes 1/1; endpoints include pod."],
                     "rollback": rollback}
    )

def scenario_liveness_probe_failure(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    restarts = random.randint(3, 15)
    pod_name, getpods = render_kubectl_get_pods(world, "CrashLoopBackOff", "0/1", restarts, f"{random.randint(3,12)}m")
    events = render_events_table([
        {"last_seen": "40s", "type": "Warning", "reason": "Unhealthy", "object": f"pod/{pod_name}",
         "message": "Liveness probe failed: Get \"http://10.0.0.12:8080/healthz\": context deadline exceeded"},
        {"last_seen": "35s", "type": "Normal", "reason": "Killing", "object": f"pod/{pod_name}",
         "message": "Container failed liveness probe, will be restarted"}
    ])
    describe = render_describe_pod(world, pod_name, "CrashLoopBackOff", restarts, events, exit_code=1, message="Liveness probe failed; container restarted")
    logs = render_logs(base, [(0, f"INFO starting {w['name']}"), (10, "WARN health endpoint slow; liveness may timeout")])
    logs = add_noise_logs(logs, base)

    diagnosis = "Container restarts due to failing liveness probe (timeout/incorrect path/slow startup)."
    fix_plan = [
        "Review liveness probe path/port/timeout; increase initialDelaySeconds for slow startups.",
        "Investigate app slowness (CPU throttling, GC pauses, dependency latency).",
        "Apply probe tuning and verify restarts stop increasing."
    ]

    actions = [
        {"type": "kubectl_edit_deploy", "cmd": f"kubectl -n {ns} edit deploy/{w['name']}  # tune livenessProbe"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]
    rollback = ["Undo probe changes if they regress detection."]

    return pack_sample(
        world,
        fault={"scenario_id": "liveness_probe_failure", "variant": "timeout", "fault_params": {}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": restarts, "oom_killed": False}},
        remediation={"category": "CrashLoopBackOff", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": ["Restarts stop increasing; pod Ready."],
                     "rollback": rollback}
    )

def scenario_rbac_forbidden(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    restarts = random.randint(1, 6)
    pod_name, getpods = render_kubectl_get_pods(world, "CrashLoopBackOff", "0/1", restarts, f"{random.randint(2,10)}m")
    events = render_events_table([{
        "last_seen": "18s", "type": "Warning", "reason": "BackOff", "object": f"pod/{pod_name}",
        "message": "Back-off restarting failed container"
    }])
    describe = render_describe_pod(world, pod_name, "CrashLoopBackOff", restarts, events, exit_code=1, message="RBAC Forbidden")
    logs = render_logs(base, [
        (0, f"INFO starting {w['name']}"),
        (4, f"ERROR Forbidden: User \"system:serviceaccount:{ns}:{w['serviceAccountName']}\" cannot list resource \"pods\" in API group \"\" in the namespace \"{ns}\""),
    ])
    logs = add_noise_logs(logs, base)

    diagnosis = "Workload fails due to RBAC permission error (Forbidden) for its ServiceAccount."
    fix_plan = [
        f"Create/update Role and RoleBinding granting least-privilege access for ServiceAccount `{w['serviceAccountName']}`.",
        "Apply RBAC and restart workload.",
        "Verify logs no longer show Forbidden and pod stabilizes."
    ]

    actions = [
        {"type": "kubectl_apply_rbac", "cmd": f"kubectl -n {ns} apply -f role.yaml -f rolebinding.yaml"},
        {"type": "kubectl_rollout_restart", "cmd": f"kubectl -n {ns} rollout restart deploy/{w['name']}"},
        {"type": "validate", "cmd": f"kubectl -n {ns} logs deploy/{w['name']} --tail=50"}
    ]
    rollback = ["Delete overly permissive RBAC and apply corrected policy."]

    return pack_sample(
        world,
        fault={"scenario_id": "rbac_forbidden", "variant": "serviceaccount_missing_role", "fault_params": {"serviceaccount": w["serviceAccountName"]}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": restarts, "oom_killed": False}},
        remediation={"category": "CrashLoopBackOff", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": ["Forbidden error disappears; pod stabilizes."],
                     "rollback": rollback}
    )

def scenario_dns_resolution_failure(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    restarts = random.randint(1, 8)
    pod_name, getpods = render_kubectl_get_pods(world, "CrashLoopBackOff", "0/1", restarts, f"{random.randint(2,10)}m")
    events = render_events_table([{
        "last_seen": "22s", "type": "Warning", "reason": "BackOff", "object": f"pod/{pod_name}",
        "message": "Back-off restarting failed container"
    }])
    describe = render_describe_pod(world, pod_name, "CrashLoopBackOff", restarts, events, exit_code=1, message="DNS lookup failed")
    dep = random.choice(["postgres.default.svc.cluster.local", "redis.default.svc.cluster.local", "kafka.observability.svc.cluster.local"])
    logs = render_logs(base, [(0, f"INFO starting {w['name']}"), (5, f"ERROR dial tcp: lookup {dep} on 10.96.0.10:53: no such host")])
    logs = add_noise_logs(logs, base)

    diagnosis = "Workload fails due to DNS resolution failure for a cluster service (no such host)."
    fix_plan = [
        "Confirm the Service name/namespace is correct and service exists (`kubectl get svc`).",
        "Check CoreDNS health and cluster DNS configuration.",
        "Fix service reference or restore DNS; restart workload and verify it resolves hostname."
    ]
    actions = [
        {"type": "inspect_service", "cmd": f"kubectl -n {ns} get svc"},
        {"type": "inspect_coredns", "cmd": "kubectl -n kube-system get pods -l k8s-app=kube-dns"},
        {"type": "validate", "cmd": f"kubectl -n {ns} logs deploy/{w['name']} --tail=50"}
    ]
    rollback = ["Revert any DNS/service config change if it worsens resolution."]

    return pack_sample(
        world,
        fault={"scenario_id": "dns_resolution_failure", "variant": "no_such_host", "fault_params": {"hostname": dep}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": restarts, "oom_killed": False}},
        remediation={"category": "CrashLoopBackOff", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": ["Hostname resolves; app connects; pod Ready."],
                     "rollback": rollback}
    )

def scenario_service_connection_refused(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    restarts = random.randint(1, 8)
    pod_name, getpods = render_kubectl_get_pods(world, "CrashLoopBackOff", "0/1", restarts, f"{random.randint(2,10)}m")
    events = render_events_table([{
        "last_seen": "24s", "type": "Warning", "reason": "BackOff", "object": f"pod/{pod_name}",
        "message": "Back-off restarting failed container"
    }])
    describe = render_describe_pod(world, pod_name, "CrashLoopBackOff", restarts, events, exit_code=1, message="connection refused")
    svc = random.choice(["redis", "postgres", "kafka"])
    host = f"{svc}.{ns}.svc.cluster.local:6379" if svc == "redis" else f"{svc}.{ns}.svc.cluster.local:5432"
    logs = render_logs(base, [(0, f"INFO starting {w['name']}"), (6, f"ERROR dial tcp {host}: connect: connection refused")])
    logs = add_noise_logs(logs, base)

    diagnosis = "App cannot connect to dependency service (connection refused) due to service/pod down or wrong port."
    fix_plan = [
        f"Check if Service `{svc}` has endpoints and backing pods are Ready.",
        "Verify the port is correct and no NetworkPolicy is blocking traffic.",
        "Restore the dependency or fix service config; restart workload and verify connection succeeds."
    ]
    actions = [
        {"type": "check_service", "cmd": f"kubectl -n {ns} get svc {svc} && kubectl -n {ns} get endpoints {svc}"},
        {"type": "check_pods", "cmd": f"kubectl -n {ns} get pods"},
        {"type": "validate", "cmd": f"kubectl -n {ns} logs deploy/{w['name']} --tail=50"}
    ]
    rollback = ["Revert any service/port changes if incorrect."]

    return pack_sample(
        world,
        fault={"scenario_id": "service_connection_refused", "variant": "dependency_down_or_port", "fault_params": {"service": svc}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": restarts, "oom_killed": False}},
        remediation={"category": "CrashLoopBackOff", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": ["Connection succeeds; pod Ready."],
                     "rollback": rollback}
    )

def scenario_quota_exceeded_pods(world):
    ns = world["namespace"]
    w = world["workload"]
    base = now_utc()

    quota = world["inventory"]["quota"]
    quota["pods_used"] = quota["pods_limit"]  # hit the limit

    pod_name, getpods = render_kubectl_get_pods(world, "Pending", "0/1", 0, f"{random.randint(1,15)}m")
    events = render_events_table([{
        "last_seen": "14s", "type": "Warning", "reason": "FailedCreate", "object": f"replicaset/{w['name']}",
        "message": f"Error creating: pods \"{pod_name}\" is forbidden: exceeded quota: pods, requested: 1, used: {quota['pods_used']}, limited: {quota['pods_limit']}"
    }])
    describe = render_describe_pod(world, pod_name, "Pending", 0, events, last_state="Waiting", exit_code=0, message="Quota exceeded")
    logs = render_logs(base, [(0, "INFO pod not created due to quota")])

    diagnosis = "Workload cannot create pods because namespace ResourceQuota for pods is exceeded."
    fix_plan = [
        "Identify which pods can be cleaned up (completed Jobs, old replicas) or increase ResourceQuota.",
        "Apply cleanup/quota change, then retry rollout.",
        f"Verify workload creates pods successfully in `{ns}`."
    ]
    actions = [
        {"type": "check_quota", "cmd": f"kubectl -n {ns} get resourcequota"},
        {"type": "cleanup", "cmd": f"kubectl -n {ns} get pods --field-selector=status.phase=Succeeded"},
        {"type": "validate", "cmd": f"kubectl -n {ns} get pods"}
    ]
    rollback = ["Revert quota increase if it causes resource pressure; prefer cleanup/rightsizing."]

    return pack_sample(
        world,
        fault={"scenario_id": "quota_exceeded_pods", "variant": "resourcequota_pods", "fault_params": {"pods_used": quota["pods_used"], "pods_limit": quota["pods_limit"]}},
        observations={"kubectl_get_pods": getpods, "kubectl_describe_pod": describe, "kubectl_get_events": events,
                      "container_logs": logs,
                      "metrics_snapshot": {"restarts": 0, "oom_killed": False}},
        remediation={"category": "FailedCreate", "diagnosis": diagnosis, "fix_plan": fix_plan,
                     "actions_structured": actions, "verification": ["New pods are created; rollout proceeds."],
                     "rollback": rollback}
    )


# ---------------------------
# Scenario Registry (15–20 scenarios)
# ---------------------------

def scenario_registry():
    # 18 original scenarios minus gitops_sync_failed (GitOps removed)
    return [
        ("crashloop_missing_secret", scenario_crashloop_missing_secret),
        ("crashloop_bad_configmap_key", scenario_crashloop_bad_configmap_key),
        ("crashloop_bad_args", scenario_crashloop_bad_args),

        ("imagepull_bad_tag", scenario_imagepull_bad_tag),
        ("imagepull_registry_auth", scenario_imagepull_registry_auth),

        ("failedscheduling_taint", scenario_failedscheduling_taint),
        ("failedscheduling_insufficient_memory", scenario_failedscheduling_insufficient_memory),
        ("failedscheduling_insufficient_cpu", scenario_failedscheduling_insufficient_cpu),
        ("nodeselector_mismatch", scenario_nodeselector_mismatch),

        ("pvc_pending_missing_storageclass", scenario_pvc_pending_missing_storageclass),
        ("pvc_not_found_mountfail", scenario_pvc_not_found_mountfail),

        ("oomkilled_limit_too_low", scenario_oomkilled_limit_too_low),

        ("readiness_probe_failure", scenario_readiness_probe_failure),
        ("liveness_probe_failure", scenario_liveness_probe_failure),

        ("rbac_forbidden", scenario_rbac_forbidden),
        ("dns_resolution_failure", scenario_dns_resolution_failure),
        ("service_connection_refused", scenario_service_connection_refused),
        ("quota_exceeded_pods", scenario_quota_exceeded_pods),
    ]


# ---------------------------
# BALANCED dataset generation
# ---------------------------

def generate_balanced_dataset(per_scenario_targets, out_source, out_sft, out_stats, max_tries_per_sample=25):
    """
    per_scenario_targets: dict scenario_id -> target_count
    """
    scenarios = dict(scenario_registry())
    counts = {sid: 0 for sid in per_scenario_targets}
    source_rows = []
    sft_rows = []
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
            sft_rows.append(to_sft_pair(sample))

    # write
    with open(out_source, "w") as f:
        for r in source_rows:
            f.write(json.dumps(r) + "\n")

    with open(out_sft, "w") as f:
        for r in sft_rows:
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

    # Choose either fixed per-scenario OR range per-scenario
    ap.add_argument("--per_scenario", type=int, default=None, help="Fixed count per scenario (e.g., 500)")
    ap.add_argument("--per_scenario_min", type=int, default=400, help="Min count per scenario (used if --per_scenario not set)")
    ap.add_argument("--per_scenario_max", type=int, default=600, help="Max count per scenario (used if --per_scenario not set)")
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    os.makedirs(args.outdir, exist_ok=True)
    out_source = os.path.join(args.outdir, "synthetic_source.jsonl")
    out_sft = os.path.join(args.outdir, "synthetic_sft.jsonl")
    out_stats = os.path.join(args.outdir, "stats.json")

    reg = scenario_registry()
    scenario_ids = [sid for sid, _ in reg]

    # Build per-scenario targets
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

    stats = generate_balanced_dataset(per_scenario_targets, out_source, out_sft, out_stats)
    print(json.dumps({
        "generated_total": stats["generated_total"],
        "out_source": out_source,
        "out_sft": out_sft,
        "out_stats": out_stats,
        "per_scenario_generated": stats["per_scenario_generated"],
    }, indent=2))


if __name__ == "__main__":
    main()