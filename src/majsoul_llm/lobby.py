"""Durable navigation state, idempotency and mutually exclusive input.

This journal is separate from the compact game archive. It contains normalized
commands and short outcomes only, never credentials, screenshots or RPC bytes.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

from .input_lease import InputBusy
from .lobby_commands import parse_command
from .lobby_session import room_facts
from .store import utcnow


class LobbyError(ValueError):
    def __init__(self, code, *, definitive=False):
        self.code = code
        self.definitive = definitive
        super().__init__(code)


class WaitingUser(LobbyError):
    pass


STAGES = {
    "stopped": {"browser_ready", "in_game"},
    "browser_ready": {"authenticating", "lobby", "in_game"},
    "authenticating": {"lobby", "in_game"},
    "lobby": {"navigating", "completed", "in_game"},
    "navigating": {"matching", "room", "in_game", "completed"},
    "matching": {"in_game", "completed"},
    "room": {"in_game", "completed"},
    "in_game": {"completed"},
    "completed": set(),
}
TERMINAL = {"succeeded", "failed", "cancelled"}


class LobbyJournal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS commands (
          id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL,
          progress TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        # A process can stop after an input reached the server but before its
        # response. Never silently replay such work when the service restarts.
        for row in self.db.execute("SELECT id,progress FROM commands").fetchall():
            progress = json.loads(row["progress"])
            if progress["status"] in ("queued", "running"):
                progress.update(status="uncertain", reason="service_restarted_check_actual_state")
                self.update(row["id"], **progress)

    def get(self, command_id):
        row = self.db.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
        if row is None:
            raise KeyError(command_id)
        return {"command":json.loads(row["body"]), **json.loads(row["progress"]), "updated_at":row["updated_at"]}

    def recent(self, limit=10):
        return [self.get(r[0]) for r in self.db.execute(
            "SELECT id FROM commands ORDER BY updated_at DESC,rowid DESC LIMIT ?", (limit,))]

    def create(self, command):
        body = json.dumps(command.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(body.encode()).hexdigest()
        row = self.db.execute("SELECT fingerprint FROM commands WHERE id=?", (command.command_id,)).fetchone()
        if row:
            if row[0] != fingerprint:
                raise LobbyError("command_id_conflict")
            return self.get(command.command_id), False
        progress = {"status":"queued", "stage":"stopped", "reason":None, "result":None,
                    "submitted_step":None, "attempt":1}
        self.db.execute("INSERT INTO commands VALUES (?,?,?,?,?)",
                        (command.command_id, fingerprint, body, json.dumps(progress), utcnow()))
        self.db.commit()
        return self.get(command.command_id), True

    def update(self, command_id, **changes):
        row = self.db.execute("SELECT progress FROM commands WHERE id=?", (command_id,)).fetchone()
        if row is None:
            raise KeyError(command_id)
        progress = {**json.loads(row[0]), **changes}
        self.db.execute("UPDATE commands SET progress=?,updated_at=? WHERE id=?",
                        (json.dumps(progress, ensure_ascii=False), utcnow(), command_id))
        self.db.commit()
        return self.get(command_id)

    def close(self):
        self.db.close()


class NavigationContext:
    def __init__(self, navigator, command_id):
        self.navigator, self.command_id = navigator, command_id
        self.room_guard = None

    def check(self):
        n = self.navigator
        if n.stop_flag.is_set():
            raise LobbyError("navigation_stopped")
        if n.game_active():
            raise LobbyError("active_game_requires_handoff")
        if n.gameplay_enabled():
            raise LobbyError("gameplay_enabled")
        if self.room_guard:
            session, code, generation = self.room_guard
            if not (session.authenticated and session.generation == generation and session.room and
                    session.room["room_code"] == code and not session.room.get("playing")):
                raise WaitingUser("joined_room_not_current_no_prepare")

    def bind_room(self, session, code):
        self.room_guard = (session, code, session.generation)
        self.check()

    def stage(self, stage):
        self.check()
        current = self.navigator.journal.get(self.command_id)["stage"]
        if stage != current and stage not in STAGES[current]:
            raise LobbyError("invalid_navigation_transition")
        self.navigator.journal.update(self.command_id, stage=stage)

    def before_submit(self, step):
        self.check()
        record = self.navigator.journal.get(self.command_id)
        previous = record.get("confirmation")
        if record["submitted_step"]:
            if not (step == "readyPlay" and record["submitted_step"] == "joinRoom" and
                    previous and previous["method"] == "joinRoom" and not previous["error_code"]):
                raise LobbyError("submission_already_recorded_check_actual_state")
        # Commit before the irreversible click. A failed click can leave a false
        # uncertainty; it must never leave a false permission to click twice.
        self.navigator.journal.update(self.command_id, submitted_step=step, confirmation=None)

    def confirmed(self, method, ack):
        record = self.navigator.journal.get(self.command_id)
        if record["submitted_step"] != method:
            raise LobbyError("confirmation_method_mismatch")
        # Persist before option validation. A correct server response can expose
        # an adapter bug; restarting must not lose the proof or repeat the click.
        confirmation = {"method":method, "error_code":ack["error_code"], "confirmed_at":utcnow()}
        if ack.get("room"):
            confirmation["room"] = room_facts(ack["room"])
        if method == "readyPlay" and "ready" in ack:
            confirmation["ready"] = ack["ready"]
        confirmations = {**record.get("confirmations", {}), method:confirmation}
        self.navigator.journal.update(self.command_id, confirmation=confirmation, confirmations=confirmations)


class Navigator:
    def __init__(self, journal, driver, lease, stop_flag, game_active, gameplay_enabled):
        self.journal, self.driver, self.lease, self.stop_flag = journal, driver, lease, stop_flag
        self.game_active, self.gameplay_enabled = game_active, gameplay_enabled
        self.task = None
        self.command_id = None

    def busy(self):
        return bool(self.task and not self.task.done())

    def status(self):
        return {"active_command":self.command_id if self.busy() else None,
                "input_owner":self.lease.owner, "capabilities":sorted(self.driver.capabilities),
                "recent":self.journal.recent()}

    def _available(self):
        if self.busy() or self.lease.owner is not None:
            raise LobbyError("input_busy")
        if self.game_active():
            raise LobbyError("active_game_requires_handoff")
        if self.gameplay_enabled():
            raise LobbyError("stop_gameplay_before_navigation")
        if getattr(getattr(self.driver, "session", None), "matching", False):
            raise LobbyError("already_matching")

    async def submit(self, data):
        command = parse_command(data)
        # Return an existing command even if another command/game is active.
        try:
            existing = self.journal.get(command.command_id)
        except KeyError:
            existing = None
        if existing:
            return self.journal.create(command)[0]
        self._available()
        if command.kind not in self.driver.capabilities:
            raise LobbyError("navigation_adapter_not_ready")
        record, _ = self.journal.create(command)
        self._launch(command, reconcile=False)
        return record

    def _launch(self, command, *, reconcile):
        self.command_id = command.command_id
        self.stop_flag.clear()
        self.task = asyncio.create_task(self._run(command, reconcile=reconcile))

    async def resume(self, command_id):
        record = self.journal.get(command_id)
        if record["status"] not in ("waiting_user", "uncertain"):
            # Duplicate resume calls return the same running/finished work.
            return record
        self._available()
        command = parse_command(record["command"])
        if command.kind not in self.driver.capabilities:
            raise LobbyError("navigation_adapter_not_ready")
        self.journal.update(command_id, status="queued", reason=None, attempt=record["attempt"]+1)
        self._launch(command, reconcile=True)
        return self.journal.get(command_id)

    async def _run(self, command, *, reconcile):
        cid = command.command_id
        ctx = NavigationContext(self, cid)
        try:
            async with self.lease.hold("navigation:"+cid):
                ctx.check()
                self.journal.update(cid, status="running")
                if reconcile:
                    # The adapter may inspect the actual page/protocol only.
                    # It cannot resubmit an uncertain create/join/match/login.
                    result = await self.driver.reconcile(command, ctx)
                else:
                    result = await self.driver.run(command, ctx)
                self.journal.update(cid, status="succeeded", reason=None, result=result)
        except asyncio.CancelledError:
            self._cancelled(cid)
            raise
        except WaitingUser as error:
            self.journal.update(cid, status="waiting_user", reason=error.code)
        except (LobbyError, InputBusy) as error:
            record = self.journal.get(cid)
            code = error.code if isinstance(error, LobbyError) else "input_busy"
            uncertain = record["submitted_step"] and not getattr(error, "definitive", False)
            self.journal.update(cid, status="uncertain" if uncertain else "failed", reason=code)
        except Exception as error:
            # Never expose arbitrary exception text from a login adapter.
            record = self.journal.get(cid)
            self.journal.update(cid, status="uncertain" if record["submitted_step"] else "failed",
                                reason="navigation_error_"+type(error).__name__)

    def _cancelled(self, cid):
        record = self.journal.get(cid)
        self.journal.update(cid, status="uncertain" if record["submitted_step"] else "cancelled",
                            reason="stopped_after_submission_check_actual_state" if record["submitted_step"] else "stopped")

    async def stop(self):
        self.stop_flag.set()
        if self.busy():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            # A task cancelled before its first instruction cannot catch its
            # own cancellation and update the journal.
            record = self.journal.get(self.command_id)
            if record["status"] in ("queued", "running"):
                self._cancelled(self.command_id)

    async def cancel(self, command_id):
        record = self.journal.get(command_id)
        if record["status"] == "succeeded" and record["stage"] in ("matching", "room", "in_game"):
            raise LobbyError("submitted_navigation_requires_visible_client_cancel")
        if self.busy() and self.command_id == command_id:
            await self.stop()
        elif record["status"] not in TERMINAL:
            self._cancelled(command_id)
        return self.journal.get(command_id)
