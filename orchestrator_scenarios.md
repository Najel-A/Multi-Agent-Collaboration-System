# Orchestrator — End-to-End Scenario Walkthroughs

Three scenarios tracing a real k8s incident through all four agents of the RCA pipeline:

```
Incident
   │
   ├── Agent 1 Solution Generator   (RCA: qwen3.5:9b)
   └── Agent 2 Solution Generator   (RCA: deepseek-r1:8b)
        │
        ▼
   Decision / Reconciliation Agent  (Executor: devstral-small-2:24b)
        │
        ▼
   Validation Agent                 (Validator: qwen3.5:35b)
        │
        ▼
   Structured RCA Result
   (diagnosis / fix_plan / commands / verification / rollback)
```

Each scenario uses a real record shape from `data/02-raw/k8s_combined_incidents.jsonl`. Corresponding automated tests live in `tests/test_orchestrator.py`.

---

## Scenario 1 — CreateContainerConfigError: missing Secret

**Use case:** a Deployment references a Kubernetes Secret (`envFrom: secretRef`) that doesn't exist in the namespace. The pod gets scheduled but the kubelet can't build the container spec, so it stalls in `CreateContainerConfigError`. Common cause: someone deployed the app before the GitOps sync brought the Secret in, or the Secret was rotated to a new name in the manifest but never created.

### Step 0 — Error ingested

```
scenario_id  : createcontainerconfigerror_missing_secret
namespace    : team-b-stg-xqqv2a
pod_name     : service-w-bm4b-0
pod_status   : Pending
event_reason : Failed
event_msg    : Error: secret "db-credentials-bm4b" not found
pod_describe : … Reason: CreateContainerConfigError
               envFrom: secretRef: db-credentials-bm4b …
pod_logs     : (empty — container never started)
```

Orchestrator pulls `pod_describe` + `pod_logs` and fires Agents 1 and 2 in parallel.

### Step 1 — Agent 1 (`qwen3.5:9b`, RCA)

**Receives:** 2-section prompt — `## kubectl describe pod` (full describe) + `## Container logs` (empty).

**Returns:**

> *"The pod cannot start because the referenced Secret 'db-credentials-bm4b' does not exist in namespace team-b-stg-xqqv2a."*

Straightforward — Qwen reads the `envFrom` line plus the event and reports the missing Secret by name.

### Step 2 — Agent 2 (`deepseek-r1:8b`, RCA)

**Receives:** same prompt (in parallel).

**Returns:**

> *"Root cause: CreateContainerConfigError — the container's envFrom references Secret 'db-credentials-bm4b' which is missing in the namespace. The pod is Pending until the Secret is created."*

DeepSeek gives the same conclusion with slightly different framing — names the error phase (`CreateContainerConfigError`) and the eventual unblocking condition. No material disagreement.

### Step 3 — Reconciler (`devstral-small-2:24b`, executor)

**Receives:** incident context + both diagnoses. Because both agents agreed, the reconciler's job is straightforward: emit the fix plan + commands.

**Returns:**

```
## Diagnosis
Pod is stuck in CreateContainerConfigError because Secret 'db-credentials-bm4b'
referenced via envFrom does not exist in namespace team-b-stg-xqqv2a.

## Fix plan
1. Create the missing Secret with the required keys
2. Kubernetes will retry container creation automatically

## Commands
- kubectl -n team-b-stg-xqqv2a create secret generic db-credentials-bm4b \
    --from-literal=DB_USER=<user> --from-literal=DB_PASSWORD=<pass>
- kubectl -n team-b-stg-xqqv2a get pod service-w-bm4b-0 -w

## Notes
Both agents identified the missing Secret. No arbitration needed.
```

Devstral's code training shines in the exact `kubectl create secret` invocation with `--from-literal` placeholders — no one has to translate "create the secret" into syntax.

### Step 4 — Validator (`qwen3.5:35b`)

**Receives:** reconciled diagnosis + fix + commands, formatted as `## Actions taken`.

**Returns:**

```
## Verification
- Secret db-credentials-bm4b exists in team-b-stg-xqqv2a
- Pod transitions from Pending to Running
- No CreateContainerConfigError events on describe

## Rollback
- kubectl -n team-b-stg-xqqv2a delete secret db-credentials-bm4b
- Note: rollback re-breaks the pod. Only use if the Secret's contents are wrong.
```

Critically: the validator warns that rolling back *re-breaks* the pod — because the pod is Pending *because* the Secret is missing. A naive rollback would undo the fix. This is the kind of guardrail you want `qwen3.5:35b` for, not a 3B model.

### Step 5 — Structured RCA Result

```
diagnosis    : (reconciled text above)
fix_plan     : [3 steps]
commands     : [2 commands, both with concrete kubectl syntax]
verification : [3 checks]
rollback     : [2 items — including the safety warning]
reconciliation_notes: "Both agents identified the missing Secret. No arbitration needed."
```

---

## Scenario 2 — CreateContainerConfigError: bad ConfigMap key

**Use case:** a Deployment references `LOG_LEVEL` from a ConfigMap that exists — but that specific key was never added to it. Much subtler than "missing ConfigMap" because `kubectl get cm` shows the map as present. The bug is a partial mismatch: schema drift between app config and the ConfigMap's data. **This scenario is where Agents 1 and 2 diverge in precision** — exactly the case the dual-RCA architecture is designed to catch.

### Step 0 — Error ingested

```
scenario_id  : createcontainerconfigerror_bad_configmap_key
namespace    : orders-prod-g1sz1z
pod_name     : web-w-a87n-57867b8c9f-b7mcl
pod_status   : Pending
event_msg    : Error: couldn't find key LOG_LEVEL in ConfigMap
               orders-prod-g1sz1z/web-config-a87n
pod_describe : … env: LOG_LEVEL: <set to key 'LOG_LEVEL' of configmap 'web-config-a87n'>
               Reason: CreateContainerConfigError …
pod_logs     : (empty)
```

### Step 1 — Agent 1 (`qwen3.5:9b`, RCA)

**Returns:**

> *"The sidecar container fails to start because a ConfigMap reference is invalid. Pod is stuck in CreateContainerConfigError."*

Qwen correctly identifies the error *class* but **under-specifies** — doesn't name the ConfigMap, doesn't name the missing key. If this went to production alone, you'd spend an extra round-trip asking "which ConfigMap? which key?". Not wrong, but not actionable.

### Step 2 — Agent 2 (`deepseek-r1:8b`, RCA)

**Returns:**

> *"ConfigMap 'web-config-a87n' exists but does not contain the key 'LOG_LEVEL' that the sidecar container's env spec requires. This is distinct from a missing ConfigMap — the map is present, the key is not."*

DeepSeek's reasoning pattern explicitly distinguishes *missing map* from *missing key* — and names both the map and the key. This is the precision difference between a general instruct model and a reasoning distill.

### Step 3 — Reconciler (`devstral-small-2:24b`, executor)

**Receives:** Agent 1's "ConfigMap issue" versus Agent 2's specific "LOG_LEVEL missing from web-config-a87n". Arbitrates.

**Returns:**

```
## Diagnosis
ConfigMap 'web-config-a87n' exists but is missing the required key 'LOG_LEVEL'.
The sidecar container cannot start because its env references a key that does
not exist in the map.

## Fix plan
1. Add the 'LOG_LEVEL' key to ConfigMap web-config-a87n
2. Trigger the pod to retry container creation (delete pod or patch configmap)
3. Verify the sidecar reaches Running

## Commands
- kubectl -n orders-prod-g1sz1z patch configmap web-config-a87n \
    --type merge -p '{"data":{"LOG_LEVEL":"info"}}'
- kubectl -n orders-prod-g1sz1z delete pod web-w-a87n-57867b8c9f-b7mcl
- kubectl -n orders-prod-g1sz1z get pods -l app=web-w-a87n

## Notes
Preferred Agent 2's framing. Agent 1 was correct but under-specified —
it identified 'ConfigMap issue' without naming the missing key, which
would have led to over-broad remediation.
```

Key outputs:

- **`patch configmap` not `create configmap`** — the map already exists, so creating it would either fail or overwrite other keys. Agent 1's vague diagnosis could easily have led to `kubectl create configmap ...` which would be the wrong fix.
- **Explicit reconciliation note** about why Agent 2 was preferred — this is the signal that feeds back to training data curation.

### Step 4 — Validator (`qwen3.5:35b`)

**Returns:**

```
## Verification
- kubectl -n orders-prod-g1sz1z get configmap web-config-a87n -o yaml shows LOG_LEVEL
- Pod sidecar status transitions from Waiting/CreateContainerConfigError to Running
- No new "couldn't find key" events

## Rollback
- kubectl -n orders-prod-g1sz1z patch configmap web-config-a87n \
    --type json -p '[{"op":"remove","path":"/data/LOG_LEVEL"}]'
- Note: rollback re-breaks the sidecar. Only roll back if LOG_LEVEL value is wrong;
  prefer patching the value instead.
```

Rollback uses JSON Patch (`remove`) rather than strategic merge — precise surgery on a single key. And the validator again warns that rolling back re-breaks the pod and recommends patching the value instead. *This is correct safety guidance that would be hard to encode in templates.*

### Step 5 — Structured RCA Result

`reconciliation_notes` explicitly records that Agent 2 was chosen for specificity. Over a large eval run, aggregating these notes tells you "Agent 2 wins on config/key precision cases" — actionable signal for fine-tuning Agent 1 with more key-specific examples.

---

## Scenario 3 — ImagePull: bad tag

**Use case:** a Deployment references an image tag that was never pushed (`worker:v9.9.9-doesnotexist`). The registry returns `manifest unknown`. **This looks superficially similar to `ImagePullAuth` but the fix path is entirely different** — the bad-tag fix is a `set image`, the auth fix is creating an `imagePullSecret`. Mixing them up means applying the wrong remediation and not fixing the underlying problem.

### Step 0 — Error ingested

```
scenario_id  : imagepull_bad_tag
namespace    : infra-lab-u7mxqy
pod_name     : worker-w-1zv4-6bc9785d9f-vkz64
pod_status   : Pending
event_reason : Failed
event_msg    : Failed to pull image "myregistry.io/worker:v9.9.9-doesnotexist":
               rpc error: not found: manifest unknown
pod_describe : … Image: myregistry.io/worker:v9.9.9-doesnotexist
               State: Waiting Reason: ImagePullBackOff …
pod_logs     : (empty — container never started)
```

### Step 1 — Agent 1 (`qwen3.5:9b`, RCA)

**Returns:**

> *"The image tag 'v9.9.9-doesnotexist' does not exist in registry myregistry.io/worker — manifest unknown. The pod cannot pull the image and is stuck in ImagePullBackOff."*

Qwen reads the event (`manifest unknown`) and the image reference in describe. Correctly identifies this as a *tag* problem, not an *auth* problem.

### Step 2 — Agent 2 (`deepseek-r1:8b`, RCA)

**Returns:**

> *"ImagePullBackOff caused by a non-existent image tag ('v9.9.9-doesnotexist'). The registry responded 'manifest unknown' which indicates the tag itself is missing — not an auth failure. Fix requires updating the deployment to a valid tag."*

DeepSeek explicitly *rules out* auth failure — the `manifest unknown` response is the distinguishing signal (auth failures surface as `unauthorized` or `401`). It also pre-stages the fix direction.

### Step 3 — Reconciler (`devstral-small-2:24b`, executor)

**Receives:** both agents agree on bad-tag vs auth. Straightforward arbitration.

**Returns:**

```
## Diagnosis
The deployment references image tag 'v9.9.9-doesnotexist' which does not exist
in registry myregistry.io/worker. Registry returns 'manifest unknown'. This is
a bad-tag error, not an authentication problem.

## Fix plan
1. Identify a valid tag that exists in the registry
2. Update the deployment to reference the valid tag
3. Wait for the new pod to pull the image successfully

## Commands
- kubectl -n infra-lab-u7mxqy set image deployment/worker-w-1zv4 \
    worker=myregistry.io/worker:<valid-tag>
- kubectl -n infra-lab-u7mxqy rollout status deployment/worker-w-1zv4
- kubectl -n infra-lab-u7mxqy get pods -l app=worker-w-1zv4

## Notes
Both agents correctly identified 'bad tag' rather than 'auth failure' —
important distinction because the fix path is entirely different
(manifest edit vs creating an imagePullSecret).
```

The reconciler's notes explicitly highlight *what was not chosen* (imagePullSecret path) — important because it's the most common wrong fix in this class. Devstral's code training emits the exact `kubectl set image` syntax with the `container-name=image-ref` pattern.

### Step 4 — Validator (`qwen3.5:35b`)

**Returns:**

```
## Verification
- New pod pulls the image successfully (no ImagePullBackOff)
- Deployment rolls out to ready replicas = desired replicas
- kubectl describe pod shows "Successfully pulled image"

## Rollback
- kubectl -n infra-lab-u7mxqy rollout undo deployment/worker-w-1zv4
- Note: rollback returns the deployment to the prior (broken) tag.
  Prefer 'set image' with a different valid tag over rollback.
```

**Subtle but critical:** `kubectl rollout undo` is the *standard* rollback for a deployment — but here, the prior state is *the broken tag we're fixing*. Rolling back would re-break the pod. The validator catches this and recommends forward-fix (`set image` to another valid tag) over reverse-rollback. This kind of context-aware rollback guidance is why you don't ship a template-based validator.

### Step 5 — Structured RCA Result

```
diagnosis    : names the tag AND explicitly rules out auth
fix_plan     : [3 steps, none involving imagePullSecrets]
commands     : [set image + rollout status + get pods]
verification : [image pulled + rollout health]
rollback     : [forward-fix preferred over rollout undo]
reconciliation_notes: "Both agents agreed. 'Bad tag' vs 'auth failure' distinction
                      matters — different fix paths."
```

---

## Comparison across the three scenarios

| | Scenario 1 (missing Secret) | Scenario 2 (bad ConfigMap key) | Scenario 3 (bad image tag) |
|---|---|---|---|
| **Both agents converge?** | yes, fully | no — Agent 2 more specific | yes — both rule out auth |
| **Primary signal** | events | events + describe env | event_message + describe |
| **Wrong fix to avoid** | — | `create configmap` | `imagePullSecret` |
| **Command type** | `create secret` | `patch configmap` | `set image` |
| **Rollback hazard** | re-breaks the pod | re-breaks the sidecar | returns to broken tag |
| **Reconciler's arbitration job** | none | pick Agent 2 over Agent 1 | confirm both on same path |
| **Value of 2nd RCA model** | low (either one works) | **high** (Agent 1 under-specifies) | medium (cross-validates) |

---

## Takeaway

The dual-RCA + reconciliation + validator pattern earns its cost on **Scenario 2** (where one model under-specifies) and on **Scenario 3's rollback** (where the standard `rollout undo` is the wrong answer). Scenario 1 is where a single model is sufficient; the other two are where the architecture pays for itself.

Each of these scenarios has a corresponding automated test in `tests/test_orchestrator.py` that encodes these behavioral expectations as assertions — so a prompt change that causes the reconciler to emit `create configmap` (Scenario 2) or `imagePullSecret` (Scenario 3) will fail CI before it hits production.
