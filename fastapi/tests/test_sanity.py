"""Sanity tests for the async 4-agent pipeline.

Uses FastAPI's TestClient (sync) — no pytest-asyncio fixtures needed.
TestClient drives the ASGI app via anyio internally and produces real
HTTP-like responses for the async handler.

Coverage:
    GET  /health                          -> 200 + agent roster
    POST /analyze (valid)                 -> 200 + all four *_output keys
    POST /analyze (custom incident_id)    -> id is echoed back
    POST /analyze (blank evidence_text)   -> 422 (Pydantic validation)
    POST /analyze (missing evidence_text) -> 422 (Pydantic validation)
    POST /analyze x2 concurrent ids       -> isolated per-incident state
    POST /analyze with payload.json       -> default sample round-trips
    POST /analyze with payloads/*.json    -> all five themed K8s scenarios

Run any of:
    python3 -m pytest tests/ -v                # via pytest, all tests
    python3 -m pytest tests/test_sanity.py -v  # via pytest, just this file
    python3 tests/test_sanity.py               # direct script, no pytest CLI knowledge needed
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# When this file is run directly (e.g. `python3 tests/test_sanity.py`),
# Python only puts `tests/` on sys.path, so `from app import app` would fail.
# Insert fastapi/ at the front so `app` resolves regardless of how we're invoked.
# (When pytest runs, conftest.py at the fastapi/ root already handles this.)
_FASTAPI_DIR_FOR_PATH = Path(__file__).resolve().parent.parent
if str(_FASTAPI_DIR_FOR_PATH) not in sys.path:
    sys.path.insert(0, str(_FASTAPI_DIR_FOR_PATH))

import pytest
from fastapi.testclient import TestClient

from app import app

_FASTAPI_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_PAYLOAD = _FASTAPI_DIR / "payload.json"
_PAYLOADS_DIR = _FASTAPI_DIR / "payloads"
_THEMED_PAYLOADS = sorted(_PAYLOADS_DIR.glob("*.json")) if _PAYLOADS_DIR.exists() else []


def _load_payload(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_health_returns_agent_list(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "fastapi-sanity"
    assert data["agents"] == ["agent_1", "agent_2", "reconciler", "validator"]


def test_analyze_returns_all_outputs(client: TestClient) -> None:
    resp = client.post(
        "/analyze",
        json={
            "evidence_text": (
                "Pod nginx-7d in ns prod is Pending; FailedScheduling: "
                "0/3 nodes available. Events show missing Secret api-keys."
            ),
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Top-level shape
    assert data["incident_id"]
    for key in (
        "agent_1_output",
        "agent_2_output",
        "reconciler_output",
        "validation_output",
        "final_recommendation",
        "requires_human_review",
    ):
        assert key in data, f"missing key: {key}"

    # Agent identities and confidence range
    assert data["agent_1_output"]["agent"] == "agent_1"
    assert data["agent_2_output"]["agent"] == "agent_2"
    assert 0.0 <= data["agent_1_output"]["confidence"] <= 1.0
    assert 0.0 <= data["agent_2_output"]["confidence"] <= 1.0
    assert data["agent_1_output"]["diagnosis"]
    assert data["agent_2_output"]["diagnosis"]

    # Reconciler chose one of the two agents and produced a fix plan + commands
    assert data["reconciler_output"]["chosen_source"] in ("agent_1", "agent_2")
    assert len(data["reconciler_output"]["fix_plan"]) > 0
    assert len(data["reconciler_output"]["commands"]) > 0

    # Validator produced verification + rollback and flagged review
    assert len(data["validation_output"]["verification"]) > 0
    assert len(data["validation_output"]["rollback"]) > 0
    assert data["validation_output"]["requires_human_review"] is True
    assert data["requires_human_review"] is True

    # Final recommendation mirrors reconciler+validator
    final = data["final_recommendation"]
    assert final["diagnosis"] == data["reconciler_output"]["diagnosis"]
    assert final["commands"] == data["reconciler_output"]["commands"]
    assert final["verification"] == data["validation_output"]["verification"]


def test_analyze_uses_supplied_incident_id(client: TestClient) -> None:
    resp = client.post(
        "/analyze",
        json={"incident_id": "custom-id-001", "evidence_text": "some k8s evidence"},
    )
    assert resp.status_code == 200
    assert resp.json()["incident_id"] == "custom-id-001"


def test_analyze_rejects_blank_evidence(client: TestClient) -> None:
    resp = client.post("/analyze", json={"evidence_text": "   "})
    assert resp.status_code == 422


def test_analyze_rejects_missing_evidence(client: TestClient) -> None:
    resp = client.post("/analyze", json={})
    assert resp.status_code == 422


def test_analyze_isolates_concurrent_incidents(client: TestClient) -> None:
    """Two requests with distinct incident ids should not collide."""
    a = client.post(
        "/analyze",
        json={"incident_id": "iso-A", "evidence_text": "evidence A"},
    )
    b = client.post(
        "/analyze",
        json={"incident_id": "iso-B", "evidence_text": "evidence B"},
    )
    assert a.status_code == 200 and b.status_code == 200
    assert a.json()["incident_id"] == "iso-A"
    assert b.json()["incident_id"] == "iso-B"


# ---------------------------------------------------------------------------
# Real-data tests — exercise the pipeline on bundled K8s payloads.
# Stubs always return canned diagnoses, so these tests check pipeline shape
# (every stage ran, response wiring intact), not diagnostic accuracy.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _DEFAULT_PAYLOAD.exists(),
    reason="payload.json not found at fastapi/payload.json",
)
def test_analyze_with_default_payload_json(client: TestClient) -> None:
    """The bundled payload.json round-trips cleanly."""
    payload = _load_payload(_DEFAULT_PAYLOAD)
    resp = client.post("/analyze", json=payload)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["incident_id"] == payload["incident_id"]
    assert data["agent_1_output"]["diagnosis"]
    assert data["agent_2_output"]["diagnosis"]
    assert data["final_recommendation"]["diagnosis"]


@pytest.mark.skipif(
    not _THEMED_PAYLOADS,
    reason="no themed payloads found in fastapi/payloads/",
)
@pytest.mark.parametrize("payload_path", _THEMED_PAYLOADS, ids=lambda p: p.stem)
def test_analyze_with_themed_payload(
    client: TestClient,
    payload_path: Path,
) -> None:
    """Each of the five themed K8s scenarios runs end-to-end.

    One PASSED line per scenario, so failures point at the exact category
    (storage / image / runtime / config / security) that broke.
    """
    payload = _load_payload(payload_path)
    resp = client.post("/analyze", json=payload)
    assert resp.status_code == 200, resp.text

    data = resp.json()

    # Incident id round-tripped from the payload file
    assert data["incident_id"] == payload["incident_id"]

    # Both RCA agents produced output
    assert data["agent_1_output"]["agent"] == "agent_1"
    assert data["agent_2_output"]["agent"] == "agent_2"
    assert data["agent_1_output"]["diagnosis"]
    assert data["agent_2_output"]["diagnosis"]

    # Reconciler picked one of them and produced a fix plan + commands
    assert data["reconciler_output"]["chosen_source"] in ("agent_1", "agent_2")
    assert len(data["reconciler_output"]["fix_plan"]) > 0
    assert len(data["reconciler_output"]["commands"]) > 0

    # Validator emitted verification + rollback
    assert len(data["validation_output"]["verification"]) > 0
    assert len(data["validation_output"]["rollback"]) > 0

    # Final recommendation is fully populated
    final = data["final_recommendation"]
    assert final["diagnosis"]
    assert final["fix_plan"]
    assert final["commands"]
    assert final["verification"]
    assert final["rollback"]

    # Human review gate is set (commands are present, so this should be True)
    assert data["requires_human_review"] is True


def test_themed_payloads_directory_is_populated() -> None:
    """Fail loudly if someone deletes the payloads/ folder by accident,
    rather than silently skipping the parametrized test above."""
    assert _PAYLOADS_DIR.exists(), f"missing directory: {_PAYLOADS_DIR}"
    found = sorted(p.name for p in _PAYLOADS_DIR.glob("*.json"))
    assert len(found) >= 5, f"expected >=5 payloads, found {len(found)}: {found}"


# ---------------------------------------------------------------------------
# Direct-script entrypoint
# ---------------------------------------------------------------------------
# `python3 tests/test_sanity.py` runs the same suite without needing the
# `pytest` CLI on PATH. Internally it just calls `pytest.main` on this file
# in verbose mode and forwards the exit code (0 = all passed, 1+ = failure).
# This is purely a UX shortcut for beginners — pytest stays the source of
# truth for fixtures, parametrization, and reporting.

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
