"""Tests for the multi-agent RCA orchestrator.

Three layers:
  1. Unit  — imports, registry, parsers (no models, instant)
  2. Stub  — full pipeline with canned SFT-shaped responses (no models, ~ms)
  3. Live  — real Ollama call (skipped when daemon unreachable)

Run with pytest:
    python3 -m pytest tests/test_orchestrator.py -v

Or standalone (no pytest required):
    python3 tests/test_orchestrator.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Ensure the repo root is importable when the test file is run directly.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.orchestrator import (
    AGENT_ROLE_MODELS,
    DEFAULT_PIPELINE,
    Orchestrator,
    StructuredRCAResult,
)
from agents.reconciliation_agent import ReconciliationAgent
from agents.solution_generator_agent import SolutionGeneratorAgent
from agents.validation_agent import ValidationAgent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_INCIDENT: dict = {
    "scenario_id":  "pvc_not_found_mountfail",
    "namespace":    "data-pipeline",
    "pod_name":     "busybox-test-pod",
    "pod_status":   "Pending",
    "pod_describe": "Name: test\nStatus: Pending\nEvents: FailedScheduling — PVC not found",
    "pod_logs":     "",
    "pod_logs_previous": "",
    "event_reason":  "FailedScheduling",
    "event_message": "persistentvolumeclaim 'missing-pvc' not found",
}

RCA_STUB = "Pod is Pending because PVC missing-pvc does not exist."

RECONCILER_STUB = """## Diagnosis
PVC does not exist.

## Fix plan
1. Create the PVC
2. Wait for binding
3. Verify pod is Running

## Commands
- kubectl apply -f pvc.yaml
- kubectl get pvc
- kubectl get pods

## Notes
Both agents agreed on PVC as root cause."""

VALIDATOR_STUB = """## Verification
- PVC becomes Bound
- Pod transitions to Running
- No FailedScheduling events

## Rollback
- Delete created PVC
- Revert manifest"""


def make_loader():
    """Loader that returns SFT-shaped canned responses per role."""
    mapping = {
        "rca":       lambda p: RCA_STUB,
        "executor":  lambda p: RECONCILER_STUB,
        "validator": lambda p: VALIDATOR_STUB,
    }
    return lambda role, name: mapping[role]


def make_scenario_loader(*, rca_1: str, rca_2: str, reconciler: str, validator: str):
    """Loader for scenario tests — returns a distinct response per model slot.

    Distinguishes Agent 1 (qwen) from Agent 2 (deepseek) by model name so each
    can emit a different diagnosis (mirrors real dual-model behavior).
    """
    def load(role: str, name: str):
        if role == "rca":
            return lambda p, _r=(rca_1 if "qwen" in name else rca_2): _r
        if role == "executor":
            return lambda p: reconciler
        if role == "validator":
            return lambda p: validator
        raise KeyError(role)
    return load


# ---------------------------------------------------------------------------
# Layer 1 — Unit tests
# ---------------------------------------------------------------------------

def test_imports():
    """All public modules import cleanly."""
    assert Orchestrator is not None
    assert StructuredRCAResult is not None
    assert SolutionGeneratorAgent is not None
    assert ReconciliationAgent is not None
    assert ValidationAgent is not None


def test_approved_model_registry_matches_sft_split():
    """Registry matches the SFT role split in generate_sft_by_role.py."""
    assert AGENT_ROLE_MODELS["rca"]       == ("qwen3.5:9b", "deepseek-r1:8b")
    assert AGENT_ROLE_MODELS["executor"]  == ("devstral-small-2:24b",)
    assert AGENT_ROLE_MODELS["validator"] == ("qwen3.5:35b", "llama3.2:3b")


def test_default_pipeline_names_are_approved():
    """All entries in DEFAULT_PIPELINE must be in the approved registry."""
    role_of = {"agent_1": "rca", "agent_2": "rca",
               "reconciler": "executor", "validator": "validator"}
    for slot, name in DEFAULT_PIPELINE.items():
        assert name in AGENT_ROLE_MODELS[role_of[slot]], (
            f"DEFAULT_PIPELINE[{slot!r}] = {name!r} is not in approved "
            f"{role_of[slot]!r} models"
        )


def test_from_role_defaults_rejects_non_approved_model():
    """Non-approved names raise ValueError with helpful message."""
    try:
        Orchestrator.from_role_defaults(
            lambda r, n: (lambda p: ""), agent_1="gpt-4",
        )
    except ValueError as e:
        assert "gpt-4" in str(e)
        assert "rca" in str(e)
        return
    raise AssertionError("expected ValueError for non-approved model")


def test_reconciler_parser_handles_markdown_and_list_prefixes():
    """Parser strips '1. ' / '- ' prefixes and separates sections."""
    parsed = ReconciliationAgent._parse_output(
        "## Diagnosis\nThe PVC is missing.\n\n"
        "## Fix plan\n1. step one\n2. step two\n\n"
        "## Commands\n- cmd one\n- cmd two\n\n"
        "## Notes\nagent 2 was clearer."
    )
    assert parsed["diagnosis"] == "The PVC is missing."
    assert parsed["fix_plan"]  == ["step one", "step two"]
    assert parsed["commands"]  == ["cmd one", "cmd two"]
    assert parsed["notes"]     == "agent 2 was clearer."


def test_validator_parser():
    """Parser extracts verification and rollback bullet lists."""
    parsed = ValidationAgent._parse_output(
        "## Verification\n- check one\n- check two\n\n"
        "## Rollback\n- revert one"
    )
    assert parsed["verification"] == ["check one", "check two"]
    assert parsed["rollback"]     == ["revert one"]


def test_solution_generator_skips_when_no_describe_or_logs():
    """Empty incident → empty diagnosis, no model call."""
    called = []
    agent = SolutionGeneratorAgent(name="agent_1", model=lambda p: called.append(p) or "x")
    result = agent.run({})
    assert result.status == "success"
    assert result.findings["diagnosis"] == ""
    assert called == []  # model must not be invoked


# ---------------------------------------------------------------------------
# Layer 2 — Stubbed end-to-end pipeline
# ---------------------------------------------------------------------------

def test_no_model_default_does_not_crash():
    """Orchestrator() with no models returns an empty-shaped result."""
    r = Orchestrator().analyze(SAMPLE_INCIDENT.copy())
    assert isinstance(r, StructuredRCAResult)
    assert r.diagnosis == "" and r.fix_plan == [] and r.commands == []
    assert r.verification == [] and r.rollback == []


def test_bootstrap_mode_wires_one_model_to_all_slots():
    """from_bootstrap() calls the single model exactly 4 times per incident."""
    calls = []
    def stub(prompt: str) -> str:
        calls.append(prompt)
        # Minimal output that parses in all three downstream parsers.
        return "## Diagnosis\nx\n## Fix plan\n- y\n## Commands\n- z\n## Verification\n- v\n## Rollback\n- r"
    o = Orchestrator.from_bootstrap(lambda role, name: stub)
    o.analyze(SAMPLE_INCIDENT.copy())
    assert len(calls) == 4, f"expected 4 model calls, got {len(calls)}"


def test_end_to_end_with_sft_shaped_stubs():
    """Full pipeline parses stub output correctly into StructuredRCAResult."""
    o = Orchestrator.from_role_defaults(make_loader())
    r = o.analyze(SAMPLE_INCIDENT.copy())

    assert r.diagnosis == "PVC does not exist."
    assert r.fix_plan  == ["Create the PVC", "Wait for binding", "Verify pod is Running"]
    assert r.commands  == [
        "kubectl apply -f pvc.yaml", "kubectl get pvc", "kubectl get pods",
    ]
    assert r.verification == [
        "PVC becomes Bound", "Pod transitions to Running", "No FailedScheduling events",
    ]
    assert r.rollback == ["Delete created PVC", "Revert manifest"]

    # Provenance — both candidate solutions must be attached.
    assert r.agent_1_solution["diagnosis"] == RCA_STUB
    assert r.agent_2_solution["diagnosis"] == RCA_STUB
    assert "agreed" in r.reconciliation_notes.lower()
    assert r.duration_ms >= 0


def test_parallel_and_sequential_agree():
    """parallel=True and parallel=False yield the same pipeline output."""
    r_par = Orchestrator.from_role_defaults(make_loader(), parallel=True).analyze(
        SAMPLE_INCIDENT.copy()
    )
    r_seq = Orchestrator.from_role_defaults(make_loader(), parallel=False).analyze(
        SAMPLE_INCIDENT.copy()
    )
    assert r_par.diagnosis    == r_seq.diagnosis
    assert r_par.fix_plan     == r_seq.fix_plan
    assert r_par.commands     == r_seq.commands
    assert r_par.verification == r_seq.verification
    assert r_par.rollback     == r_seq.rollback


def test_analyze_does_not_leak_internal_keys():
    """Scratch keys used to pass data between agents must be popped."""
    incident = SAMPLE_INCIDENT.copy()
    Orchestrator.from_role_defaults(make_loader()).analyze(incident)
    for k in ("_agent_1_solution", "_agent_2_solution", "_reconciled_solution"):
        assert k not in incident, f"{k} leaked into caller's dict"


def test_analyze_batch_returns_one_result_per_incident():
    """analyze_batch preserves the input order."""
    incidents = [
        dict(SAMPLE_INCIDENT, pod_name=f"pod-{i}") for i in range(3)
    ]
    results = Orchestrator.from_role_defaults(make_loader()).analyze_batch(
        incidents, max_workers=2
    )
    assert len(results) == 3
    assert [r.incident_id for r in results] == ["pod-0", "pod-1", "pod-2"]


def test_real_incident_from_combined_jsonl_if_present():
    """Smoke-test against a real record from k8s_combined_incidents.jsonl."""
    path = ROOT / "data" / "02-raw" / "k8s_combined_incidents.jsonl"
    if not path.exists():
        return  # dataset not present — skip silently
    with open(path) as f:
        incident = json.loads(f.readline())
    r = Orchestrator.from_role_defaults(make_loader()).analyze(incident)
    assert r.incident_id, "incident_id should be populated from pod_name/scenario_id"
    assert r.diagnosis == "PVC does not exist."


# ---------------------------------------------------------------------------
# Layer 3 — Live integration (opt-in)
# ---------------------------------------------------------------------------

def _ollama_available(base_url: str = "http://localhost:11434") -> bool:
    try:
        with urllib.request.urlopen(base_url + "/api/tags", timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False


def test_live_ollama_round_trip():
    """End-to-end against a real local Ollama.

    Skipped when:
      - RUN_LIVE is not set, OR
      - Ollama daemon isn't reachable.
    Set RUN_LIVE=1 to enable. Requires `ollama pull qwen3.5:9b` first
    (or pass a different model via OLLAMA_MODEL).
    """
    if not os.environ.get("RUN_LIVE"):
        return
    if not _ollama_available():
        print("  (skipped: Ollama not reachable at localhost:11434)")
        return
    from agents.model_loaders import ollama_loader

    model = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
    o = Orchestrator.from_bootstrap(ollama_loader(), model=model)
    r = o.analyze(SAMPLE_INCIDENT.copy())

    # We can't assert exact content against a live LLM, but we can assert shape.
    assert r.diagnosis, "live model returned empty diagnosis"
    assert r.duration_ms > 0


# ---------------------------------------------------------------------------
# Scenario tests — full pipeline per error class
# ---------------------------------------------------------------------------

def test_scenario_createcontainerconfigerror_missing_secret():
    """Pod fails to start because a referenced Secret does not exist.

    Signal: CreateContainerConfigError with 'secret "<name>" not found' in
    events. Both RCA agents should converge on the secret being absent.
    """
    incident = {
        "scenario_id": "createcontainerconfigerror_missing_secret",
        "namespace":   "team-b-stg-xqqv2a",
        "pod_name":    "service-w-bm4b-0",
        "pod_status":  "Pending",
        "event_reason":  "Failed",
        "event_message": 'Error: secret "db-credentials-bm4b" not found',
        "pod_describe": (
            "Name: service-w-bm4b-0\nNamespace: team-b-stg-xqqv2a\n"
            "Status: Pending\nContainers:\n  main:\n"
            "    State: Waiting\n    Reason: CreateContainerConfigError\n"
            "    Env From:\n      secretRef: db-credentials-bm4b\n"
            "Events:\n  Warning Failed ... Error: secret \"db-credentials-bm4b\" not found"
        ),
        "pod_logs": "",
        "pod_logs_previous": "",
    }

    rca_1 = (
        "The pod cannot start because the referenced Secret "
        "'db-credentials-bm4b' does not exist in namespace team-b-stg-xqqv2a."
    )
    rca_2 = (
        "Root cause: CreateContainerConfigError — the container's envFrom "
        "references Secret 'db-credentials-bm4b' which is missing in the "
        "namespace. The pod is Pending until the Secret is created."
    )
    reconciler = """## Diagnosis
Pod is stuck in CreateContainerConfigError because Secret 'db-credentials-bm4b' referenced via envFrom does not exist in namespace team-b-stg-xqqv2a.

## Fix plan
1. Create the missing Secret with the required keys
2. Kubernetes will retry container creation automatically

## Commands
- kubectl -n team-b-stg-xqqv2a create secret generic db-credentials-bm4b --from-literal=DB_USER=<user> --from-literal=DB_PASSWORD=<pass>
- kubectl -n team-b-stg-xqqv2a get pod service-w-bm4b-0 -w

## Notes
Both agents identified the missing Secret. No arbitration needed."""
    validator = """## Verification
- Secret db-credentials-bm4b exists in team-b-stg-xqqv2a
- Pod transitions from Pending to Running
- No CreateContainerConfigError events on describe

## Rollback
- kubectl -n team-b-stg-xqqv2a delete secret db-credentials-bm4b
- Note: rollback re-breaks the pod. Only use if the Secret's contents are wrong."""

    loader = make_scenario_loader(
        rca_1=rca_1, rca_2=rca_2, reconciler=reconciler, validator=validator,
    )
    r = Orchestrator.from_role_defaults(loader).analyze(incident)

    # Diagnosis mentions the missing Secret
    assert "db-credentials-bm4b" in r.diagnosis
    assert "does not exist" in r.diagnosis.lower() or "missing" in r.diagnosis.lower()

    # Fix plan and commands are actionable
    assert len(r.fix_plan) >= 2
    assert any("kubectl" in c and "secret" in c.lower() for c in r.commands)
    assert any("create secret" in c for c in r.commands)

    # Validation covers the right checks
    assert len(r.verification) >= 2
    assert any("db-credentials-bm4b" in v or "Secret" in v for v in r.verification)

    # Rollback is safety-aware (not just "revert")
    assert any("re-break" in rb.lower() or "only use if" in rb.lower() for rb in r.rollback)

    # Both candidate diagnoses attached for the feedback loop
    assert r.agent_1_solution["diagnosis"] == rca_1
    assert r.agent_2_solution["diagnosis"] == rca_2
    assert "agreed" in r.reconciliation_notes.lower() or "no arbitration" in r.reconciliation_notes.lower()


def test_scenario_createcontainerconfigerror_bad_configmap_key():
    """Pod fails because a referenced ConfigMap key does not exist.

    Subtler than a missing ConfigMap: the ConfigMap itself is present, but
    one of the keys the container reads from is absent. Tests that the
    pipeline distinguishes 'missing map' from 'missing key'.
    """
    incident = {
        "scenario_id": "createcontainerconfigerror_bad_configmap_key",
        "namespace":   "orders-prod-g1sz1z",
        "pod_name":    "web-w-a87n-57867b8c9f-b7mcl",
        "pod_status":  "Pending",
        "event_reason":  "Failed",
        "event_message": (
            'Error: couldn\'t find key LOG_LEVEL in ConfigMap '
            'orders-prod-g1sz1z/web-config-a87n'
        ),
        "pod_describe": (
            "Name: web-w-a87n-57867b8c9f-b7mcl\nNamespace: orders-prod-g1sz1z\n"
            "Status: Pending\nContainers:\n  sidecar:\n"
            "    State: Waiting\n    Reason: CreateContainerConfigError\n"
            "    Environment:\n      LOG_LEVEL: <set to key 'LOG_LEVEL' of configmap 'web-config-a87n'>\n"
            "Events:\n  Warning Failed ... couldn't find key LOG_LEVEL in ConfigMap web-config-a87n"
        ),
        "pod_logs": "",
        "pod_logs_previous": "",
    }

    # Agent 1 under-specifies — says "ConfigMap issue" but not which key.
    rca_1 = (
        "The sidecar container fails to start because a ConfigMap reference "
        "is invalid. Pod is stuck in CreateContainerConfigError."
    )
    # Agent 2 names the specific missing key — more precise.
    rca_2 = (
        "ConfigMap 'web-config-a87n' exists but does not contain the key "
        "'LOG_LEVEL' that the sidecar container's env spec requires. This "
        "is distinct from a missing ConfigMap — the map is present, the key "
        "is not."
    )
    reconciler = """## Diagnosis
ConfigMap 'web-config-a87n' exists but is missing the required key 'LOG_LEVEL'. The sidecar container cannot start because its env references a key that does not exist in the map.

## Fix plan
1. Add the 'LOG_LEVEL' key to ConfigMap web-config-a87n
2. Trigger the pod to retry container creation (delete pod or patch configmap)
3. Verify the sidecar reaches Running

## Commands
- kubectl -n orders-prod-g1sz1z patch configmap web-config-a87n --type merge -p '{"data":{"LOG_LEVEL":"info"}}'
- kubectl -n orders-prod-g1sz1z delete pod web-w-a87n-57867b8c9f-b7mcl
- kubectl -n orders-prod-g1sz1z get pods -l app=web-w-a87n

## Notes
Preferred Agent 2's framing. Agent 1 was correct but under-specified — it identified 'ConfigMap issue' without naming the missing key, which would have led to over-broad remediation."""
    validator = """## Verification
- kubectl -n orders-prod-g1sz1z get configmap web-config-a87n -o yaml shows LOG_LEVEL
- Pod sidecar status transitions from Waiting/CreateContainerConfigError to Running
- No new "couldn't find key" events

## Rollback
- kubectl -n orders-prod-g1sz1z patch configmap web-config-a87n --type json -p '[{"op":"remove","path":"/data/LOG_LEVEL"}]'
- Note: rollback re-breaks the sidecar. Only roll back if LOG_LEVEL value is wrong; prefer patching the value instead."""

    loader = make_scenario_loader(
        rca_1=rca_1, rca_2=rca_2, reconciler=reconciler, validator=validator,
    )
    r = Orchestrator.from_role_defaults(loader).analyze(incident)

    # Diagnosis must distinguish missing-key from missing-map
    assert "LOG_LEVEL" in r.diagnosis
    assert "web-config-a87n" in r.diagnosis
    assert "key" in r.diagnosis.lower()

    # Commands must patch the ConfigMap, not create a new one
    assert any("patch configmap" in c for c in r.commands)
    assert not any("create configmap" in c for c in r.commands), (
        "Should patch the existing ConfigMap, not create a new one"
    )

    # Verification confirms the key was added
    assert any("LOG_LEVEL" in v for v in r.verification)

    # Reconciler explicitly preferred Agent 2's specificity
    notes = r.reconciliation_notes.lower()
    assert "agent 2" in notes
    assert "agent 2" in notes and ("under-specified" in notes or "precise" in notes or "preferred" in notes)


def test_scenario_imagepull_bad_tag():
    """Pod fails because the image tag does not exist in the registry.

    Distinct from ImagePullAuth (which would say 'unauthorized'): bad tag
    surfaces as 'manifest unknown' or 'not found'. The fix is a manifest edit,
    not a credentials change — the validator must get the rollback right.
    """
    incident = {
        "scenario_id": "imagepull_bad_tag",
        "namespace":   "infra-lab-u7mxqy",
        "pod_name":    "worker-w-1zv4-6bc9785d9f-vkz64",
        "pod_status":  "Pending",
        "event_reason":  "Failed",
        "event_message": (
            'Failed to pull image "myregistry.io/worker:v9.9.9-doesnotexist": '
            'rpc error: not found: manifest unknown'
        ),
        "pod_describe": (
            "Name: worker-w-1zv4-6bc9785d9f-vkz64\nNamespace: infra-lab-u7mxqy\n"
            "Status: Pending\nContainers:\n  worker:\n"
            "    Image: myregistry.io/worker:v9.9.9-doesnotexist\n"
            "    State: Waiting\n    Reason: ImagePullBackOff\n"
            "Events:\n  Warning Failed ... manifest unknown"
        ),
        "pod_logs": "",
        "pod_logs_previous": "",
    }

    rca_1 = (
        "The image tag 'v9.9.9-doesnotexist' does not exist in registry "
        "myregistry.io/worker — manifest unknown. The pod cannot pull the "
        "image and is stuck in ImagePullBackOff."
    )
    rca_2 = (
        "ImagePullBackOff caused by a non-existent image tag "
        "('v9.9.9-doesnotexist'). The registry responded 'manifest unknown' "
        "which indicates the tag itself is missing — not an auth failure. "
        "Fix requires updating the deployment to a valid tag."
    )
    reconciler = """## Diagnosis
The deployment references image tag 'v9.9.9-doesnotexist' which does not exist in registry myregistry.io/worker. Registry returns 'manifest unknown'. This is a bad-tag error, not an authentication problem.

## Fix plan
1. Identify a valid tag that exists in the registry
2. Update the deployment to reference the valid tag
3. Wait for the new pod to pull the image successfully

## Commands
- kubectl -n infra-lab-u7mxqy set image deployment/worker-w-1zv4 worker=myregistry.io/worker:<valid-tag>
- kubectl -n infra-lab-u7mxqy rollout status deployment/worker-w-1zv4
- kubectl -n infra-lab-u7mxqy get pods -l app=worker-w-1zv4

## Notes
Both agents correctly identified 'bad tag' rather than 'auth failure' — important distinction because the fix path is entirely different (manifest edit vs creating an imagePullSecret)."""
    validator = """## Verification
- New pod pulls the image successfully (no ImagePullBackOff)
- Deployment rolls out to ready replicas = desired replicas
- kubectl describe pod shows "Successfully pulled image"

## Rollback
- kubectl -n infra-lab-u7mxqy rollout undo deployment/worker-w-1zv4
- Note: rollback returns the deployment to the prior (broken) tag. Prefer 'set image' with a different valid tag over rollback."""

    loader = make_scenario_loader(
        rca_1=rca_1, rca_2=rca_2, reconciler=reconciler, validator=validator,
    )
    r = Orchestrator.from_role_defaults(loader).analyze(incident)

    # Diagnosis must name the bad tag and distinguish from auth failure
    assert "v9.9.9-doesnotexist" in r.diagnosis or "manifest unknown" in r.diagnosis.lower()
    assert "auth" not in r.diagnosis.lower() or "not an auth" in r.diagnosis.lower()

    # Commands must set the image, not touch imagePullSecrets
    assert any("set image" in c for c in r.commands)
    assert not any("imagepullsecret" in c.lower() for c in r.commands), (
        "Bad-tag fix should not touch imagePullSecrets (that's for ImagePullAuth)"
    )

    # Verification targets ImagePullBackOff and rollout health
    assert any("ImagePull" in v or "pulled image" in v.lower() for v in r.verification)
    assert any("rollout" in v.lower() or "replicas" in v.lower() for v in r.verification)

    # Rollback correctly warns that undoing returns to the broken tag
    assert any("prior" in rb.lower() or "broken" in rb.lower() or "prefer" in rb.lower()
               for rb in r.rollback)

    # Both agents must have been called with the same prompt shape
    assert r.agent_1_solution["diagnosis"] == rca_1
    assert r.agent_2_solution["diagnosis"] == rca_2


# ---------------------------------------------------------------------------
# Standalone runner (no pytest required)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    tests = sorted(name for name in globals() if name.startswith("test_"))
    passed = 0
    failed: list[str] = []
    for name in tests:
        try:
            globals()[name]()
            print(f"  ok   {name}")
            passed += 1
        except Exception as e:
            print(f"FAIL   {name}: {e}")
            traceback.print_exc()
            failed.append(name)
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        raise SystemExit(1)
