"""Blackboard — the shared message bus for the multi-agent pipeline.

A Blackboard is a thread-safe, topic-partitioned message log. Agents
write Messages to topics; other components subscribe to topics and react
when something is posted. The Orchestrator uses one Blackboard per
analyze() call to drive the protocol — post incident, collect bids,
dispatch winners, gather diagnoses, signal conflicts, deliver fix plans.

Design notes:
- One Blackboard per analyze() call; no cross-request state.
- Writes are protected by a re-entrant lock.
- Payloads are deep-copied at write time so later mutations of the
  caller's source dict don't leak into the audit trail.
- Subscribers are notified outside the lock; exceptions in subscribers
  do not poison the writer.
- trace() returns a chronological log of every message — used to
  populate StructuredRCAResult.trace.
"""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Topic names — the canonical vocabulary for the multi-agent protocol.
# ---------------------------------------------------------------------------

class Topics:
    INCIDENT    = "incident"      # raw incident posted at the start of analyze()
    BID_REQUEST = "bid_request"   # triage opens bidding round
    BID         = "bid"           # one per RCA-eligible agent
    DISPATCH    = "dispatch"      # orchestrator's selected agents
    DIAGNOSIS   = "diagnosis"     # one per dispatched agent
    CONFLICT    = "conflict"      # posted when diagnoses disagree
    FIX_PLAN    = "fix_plan"      # reconciler's resolved plan
    VALIDATION  = "validation"    # validator's verification + rollback


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Message:
    """A single posting on the Blackboard."""
    topic: str
    sender: str
    payload: dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic":     self.topic,
            "sender":    self.sender,
            "timestamp": self.timestamp,
            "payload":   self.payload,
        }


Subscriber = Callable[[Message], None]


# ---------------------------------------------------------------------------
# Blackboard
# ---------------------------------------------------------------------------

class Blackboard:
    """Thread-safe pub/sub log used as shared memory for one pipeline run."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_topic: dict[str, list[Message]] = {}
        self._subscribers: dict[str, list[Subscriber]] = {}
        self._all: list[Message] = []

    def write(self, msg: Message) -> None:
        """Post a message to its topic and notify subscribers.

        The payload is deep-copied so subsequent mutations of the source
        dict (e.g. the orchestrator's work copy of the incident) don't
        leak into the audit trail.
        """
        snapshot = Message(
            topic=msg.topic,
            sender=msg.sender,
            payload=copy.deepcopy(msg.payload),
            timestamp=msg.timestamp,
        )
        with self._lock:
            self._by_topic.setdefault(snapshot.topic, []).append(snapshot)
            self._all.append(snapshot)
            subs = list(self._subscribers.get(snapshot.topic, []))
        # Notify outside the lock to avoid deadlock if a callback writes back.
        for cb in subs:
            try:
                cb(snapshot)
            except Exception:  # noqa: BLE001
                # A misbehaving subscriber must not poison the writer.
                pass

    def read(self, topic: str) -> list[Message]:
        """Return all messages for a topic, in posting order."""
        with self._lock:
            return list(self._by_topic.get(topic, []))

    def subscribe(self, topic: str, callback: Subscriber) -> None:
        """Register a callback that fires synchronously on each new write."""
        with self._lock:
            self._subscribers.setdefault(topic, []).append(callback)

    def trace(self) -> list[Message]:
        """Return the chronological log of every message."""
        with self._lock:
            return list(self._all)
