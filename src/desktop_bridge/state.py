"""Single-writer leases, fresh observations, and durable action receipts.

A restarted process never replays a possibly completed side effect. Unknown
outcomes remain explicit and require a fresh observation and a NEW action ID.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BridgeError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Observation:
    epoch: int
    at: float
    browser_tab_id: str | None = None
    browser_tab_ids: tuple[str, ...] = ()


class Session:
    def __init__(self, database: Path, observation_ttl: float = 30):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS receipts "
            "(id TEXT PRIMARY KEY, fingerprint TEXT, state TEXT, result TEXT, at REAL)"
        )
        self.db.execute("UPDATE receipts SET state='unknown' WHERE state='running'")
        self.db.commit()
        self.id = str(uuid.uuid4())
        self.mode = "ready"  # First session_start is allowed; a user pause cannot be overridden.
        self.epoch = 0
        self.lock = asyncio.Lock()
        self.control_pending = False
        self.human_input_pending = 0
        self.human_holds: set[object] = set()
        self.human_revision = 0
        self.observations: dict[str, Observation] = {}
        self.observation_ttl = observation_ttl
        self.last_activity = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self.sockets: set = set()
        self.shared_sockets: set = set()

    def status(self):
        return {
            "session_id": self.id,
            "state": self.mode.upper(),
            "epoch": self.epoch,
            "in_flight": self.lock.locked() or self.control_pending,
            "human_input_pending": bool(self.human_input_pending),
            "human_input_active": bool(self.human_input_pending or self.human_holds),
            "resolution": {"width": 1280, "height": 800},
            "capabilities": {
                "memory_resume": False,
                "persistent_workspace": True,
                "single_user": True,
                "autonomous_model_loop": False,
            },
        }

    def event(self, kind: str, detail: str):
        # Do not retain arbitrary text, keystrokes, screenshots, or shell commands.
        self.events.append({"time": time.time(), "kind": kind, "detail": detail})
        self.events = self.events[-100:]

    async def transition(self, mode: str):
        if mode not in {"agent", "human", "private", "paused", "stopped"}:
            raise BridgeError("INVALID_STATE", "Unknown control state")
        # Revoke immediately, BEFORE waiting for any in-flight operation.
        self.mode = mode
        self.epoch += 1
        self.observations.clear()
        self.last_activity = time.monotonic()
        for socket in self.sockets - self.shared_sockets:
            try:
                await socket.close(code=1008, reason="Control changed; reconnect")
            except Exception:
                pass
        self.event("control", mode)
        return self.status()

    @asynccontextmanager
    async def human_input(self, source=None):
        # Mark intent BEFORE waiting: queued agent calls must never jump ahead.
        # This is an input turn, not a mode change or a cancellation of shell work.
        self.human_input_pending += 1
        self.human_revision += 1
        self.epoch += 1
        self.observations.clear()
        try:
            async with self.lock:
                if source is not None and self.human_holds - {source}:
                    raise BridgeError("HUMAN_INPUT_ACTIVE", "Finish input in the other viewer first")
                yield
        finally:
            self.human_input_pending -= 1
            self.epoch += 1
            self.observations.clear()
            self.last_activity = time.monotonic()

    def require_input_idle(self):
        if self.human_input_pending or self.human_holds:
            raise BridgeError(
                "HUMAN_INPUT_ACTIVE",
                "Human input is in progress. Observe again after it finishes; do not replay a queued action.",
            )

    def observe(self, *, browser_tab_id=None, browser_tab_ids=()):
        if self.mode == "private":
            raise BridgeError("PRIVATE_TAKEOVER", "Observation paused for private human takeover")
        self.require_input_idle()
        now = time.monotonic()
        self.observations = {
            k: v for k, v in self.observations.items() if now - v.at <= self.observation_ttl
        }
        if len(self.observations) >= 128:
            self.observations.pop(next(iter(self.observations)))
        identifier = str(uuid.uuid4())
        self.observations[identifier] = Observation(
            self.epoch, now, browser_tab_id, tuple(browser_tab_ids)
        )
        return identifier

    def require_agent(self):
        if self.mode != "agent":
            raise BridgeError("CONTROL_NOT_OWNED", f"Agent cannot act while session is {self.mode}")
        self.require_input_idle()

    @asynccontextmanager
    async def action(self, action_id: str, payload: dict, observation_id: str | None = None):
        if not action_id or len(action_id) > 128:
            raise BridgeError("INVALID_ACTION_ID", "Use a unique action_id of 1–128 characters")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        self.require_input_idle()
        human_revision = self.human_revision
        async with self.lock:
            self.require_agent()
            if human_revision != self.human_revision:
                raise BridgeError(
                    "CONTROL_CHANGED", "Human input superseded this queued action. Inspect before trying a new action."
                )
            row = self.db.execute(
                "SELECT fingerprint,state,result FROM receipts WHERE id=?", (action_id,)
            ).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise BridgeError(
                        "IDEMPOTENCY_CONFLICT", "action_id was used for another action"
                    )
                if row[1] != "done":
                    raise BridgeError(
                        "OUTCOME_UNKNOWN", "Do not replay: inspect external state first"
                    )
                yield {"cached": json.loads(row[2])}
                return
            observed = None
            if observation_id is not None:
                observed = self.observations.get(observation_id)
                if (
                    not observed
                    or observed.epoch != self.epoch
                    or time.monotonic() - observed.at > self.observation_ttl
                ):
                    raise BridgeError(
                        "STALE_OBSERVATION", "Take a fresh screenshot or browser snapshot"
                    )
            self.db.execute(
                "INSERT INTO receipts VALUES (?,?, 'running',NULL,?)",
                (action_id, fingerprint, time.time()),
            )
            self.db.commit()
            ticket: dict[str, Any] = {"observation": observed}
            self.last_activity = time.monotonic()
            try:
                yield ticket
                result = ticket.get("result", {"ok": True})
                self.db.execute(
                    "UPDATE receipts SET state='done',result=? WHERE id=?",
                    (json.dumps(result), action_id),
                )
                self.event("action", str(payload.get("tool", "action")))
            except BaseException:
                self.db.execute("UPDATE receipts SET state='unknown' WHERE id=?", (action_id,))
                raise
            finally:
                self.epoch += 1
                self.observations.clear()
                self.db.commit()

    def close(self):
        self.db.close()
