"""In-memory incident blackboard.

Single-process, asyncio-only — no Redis, no IPC. Agents coordinate by
writing typed payloads under a stable incident_id. Each incident gets
its own asyncio.Lock so two concurrent /analyze requests never serialize
on each other.

The registry-level dictionaries (`_store`, `_locks`) are NOT guarded by a
lock. Their critical sections (init, discard) contain no `await`, so under
cooperative scheduling no other task can interleave between the
membership check and the dict mutation. Per-incident locks remain so that
future agent code can do read-modify-write across `await` points safely.
"""

from __future__ import annotations

import asyncio
from typing import Any


class IncidentBlackboard:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def init(self, incident_id: str, evidence_text: str) -> None:
        """Register a new incident. Raises if the id is already in use."""
        if incident_id in self._store:
            raise ValueError(f"incident already exists: {incident_id!r}")
        self._locks[incident_id] = asyncio.Lock()
        self._store[incident_id] = {"evidence_text": evidence_text}

    async def write(self, incident_id: str, key: str, value: Any) -> None:
        async with self._lock_for(incident_id):
            self._store[incident_id][key] = value

    async def read(self, incident_id: str, key: str) -> Any:
        async with self._lock_for(incident_id):
            return self._store[incident_id].get(key)

    async def snapshot(self, incident_id: str) -> dict[str, Any]:
        """Return a shallow copy of the incident's state."""
        async with self._lock_for(incident_id):
            return dict(self._store[incident_id])

    async def discard(self, incident_id: str) -> None:
        """Drop an incident's state — call after responding to keep memory bounded."""
        self._store.pop(incident_id, None)
        self._locks.pop(incident_id, None)

    def _lock_for(self, incident_id: str) -> asyncio.Lock:
        lock = self._locks.get(incident_id)
        if lock is None:
            raise KeyError(f"unknown incident_id: {incident_id!r}")
        return lock
