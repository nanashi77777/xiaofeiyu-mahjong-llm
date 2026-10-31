"""Sanitized protocol → idempotent authoritative state. No model writes here."""
from __future__ import annotations

import asyncio
import hashlib
import time

from .domain import Event, Phase, Rules, Snapshot
from .game_modes import FOUR_PLAYER_LENGTHS
from .lobby_session import LobbySession
from .state import apply_event, digest
from .store import Store, packed, utcnow


def room_rules(config: dict) -> Rules:
    mode = config.get("mode", {})
    detail = mode.get("detail_rule", {})
    meta = config.get("meta", {})
    category = config.get("category", 0)
    room = "friend" if meta.get("room_id") or category == 1 else "ranked" if meta.get("mode_id") or category == 2 else "contest" if meta.get("contest_uid") or category == 4 else "unknown"
    exotic = ("guyi_mode", "begin_open_mode", "jiuchao_mode", "muyu_mode", "open_hand", "xuezhandaodi",
              "huansanzhang", "chuanma", "field_spell_mode", "zhanxing", "tianming_mode", "enable_shiti",
              "yongchang_mode", "hunzhiyiji_mode", "wanxiangxiuluo_mode", "beishuizhizhan_mode")
    unsupported = [key for key in exotic if detail.get(key)]
    # Length values are verified from visible four-player room creation. AI
    # capability alone does not imply a different length or nonstandard rules.
    if mode.get("mode") not in FOUR_PLAYER_LENGTHS.values():
        unsupported.append("unverified_four_player_mode")
    if detail.get("enable_nontsumo_liqi") or detail.get("enable_renhe"):
        unsupported.append("nonstandard_yaku_option")
    return Rules(room_type=room, mode_id=meta.get("mode_id"), raw={"category": category, "mode": mode, "meta": {k: meta[k] for k in ("mode_id",) if k in meta}},
                 open_tanyao=bool(detail.get("shiduan", 1)), aka_dora=bool(detail.get("dora_count", 3)),
                 double_yakuman=not bool(detail.get("disable_double_yakuman")),
                 composite_yakuman=not bool(detail.get("disable_composite_yakuman")),
                 counted_yakuman=not bool(detail.get("disable_leijiyiman")),
                 kiriage=bool(detail.get("have_qieshangmanguan")), starting_points=detail.get("init_point"),
                 unsupported=unsupported, unknown_fields=[] if detail else ["detail_rule_missing"])


class Engine:
    def __init__(self, store: Store, protocol=None, on_state=None):
        self.store, self.protocol, self.on_state = store, protocol, on_state
        self.state: Snapshot | None = None
        self.auth: dict[str, dict] = {}  # ephemeral account→seat mapping, never serialized
        self.connection: str | None = None
        self.round_key: str | None = None
        self.lock = asyncio.Lock()
        self.last_error: str | None = None
        self.input_windows = {}
        self.on_input = None
        self.lobby_session = LobbySession()

    def browser_reset(self):
        self.lobby_session.reset()

    def socket_closed(self, connection):
        self.lobby_session.closed(connection)

    async def frame(self, connection, raw, from_client):
        if not self.protocol:
            self.last_error = "protocol_not_loaded"
            return
        try:
            message = self.protocol.frame(connection, raw, from_client)
            if message:
                await self.message(message)
        except Exception as error:
            await self.gap(type(error).__name__)

    async def gap(self, reason):
        self.last_error = reason
        if self.state:
            self.state = self.state.model_copy(deep=True)
            self.state.consistent = False
            self.state.completeness = "gaps"
            self.state.operation = None
            self.store.save_snapshot(self.state)
            self.store.record("input_gap", {"error_type": reason}, self.state.match_id, self.state.round_uid)
            await self.changed(None)

    async def changed(self, event):
        if self.on_state:
            await self.on_state(self.state, event)

    async def message(self, message):
        async with self.lock:
            if message["method"].startswith(".lq.Lobby."):
                self.lobby_session.observe(message)
                return
            method, direction = message["method"].rsplit(".", 1)[-1], message["direction"]
            data, connection = message["data"], message["connection"]
            if method == "authGame" and direction == "request":
                self.auth[connection] = data
                return
            if method == "authGame" and direction == "response":
                auth = self.auth.pop(connection, None)
                if not auth or data.get("error", {}).get("code"):
                    return
                seats = data.get("seat_list", [])
                if len(seats) != 4 or auth["account_id"] not in seats:
                    raise ValueError("not an authenticated four-player seat")
                self.connection = connection
                match_id = auth["game_uuid"]
                if self.state and self.state.match_id != match_id and self.state.lifecycle == "active":
                    await self.interrupt("different_match_authenticated")
                previous = self.store.latest(match_id)
                self.state = previous or Snapshot(match_id=match_id, self_seat=seats.index(auth["account_id"]))
                self.state.operation = None
                self.state.rules = room_rules(data.get("game_config", {}))
                self.state.phase = Phase.RECOVERING if previous else Phase.WAITING
                self.round_key = self.state.context.get("source_round_key")
                await self.changed(None)
                return
            if not self.state or connection != self.connection:
                return
            if direction == "request":
                # Actual input request is evidence only; state changes on server events.
                if method in ("inputOperation", "inputChiPengGang"):
                    window=self.state.operation.window_id if self.state.operation else None
                    self.input_windows[(connection,message.get("request_id"))]=window
                    self.store.record("client_input", {**data,"window_id":window}, self.state.match_id, self.state.round_uid)
                    if self.on_input:
                        await self.on_input("request",window,data,None)
                return
            if direction=="response" and method in ("inputOperation","inputChiPengGang"):
                window=self.input_windows.pop((connection,message.get("request_id")),None)
                original=message.get("original_input") or {}
                ack_id=self.store.record("server_input_ack",{"window_id":window,"input":original,"accepted":not bool(data.get("error",{}).get("code"))},self.state.match_id,self.state.round_uid)
                if self.on_input:
                    await self.on_input("accepted" if not data.get("error",{}).get("code") else "rejected",window,original,ack_id)
                return
            restore = data.get("game_restore")
            if restore:
                await self.restore(restore)
                return
            if method == "ActionPrototype":
                await self.action(data["name"], data["data"], data.get("step"))
            elif method in ("NotifyGameEndResult", "NotifyGameTerminate"):
                await self.action(method, data, None)

    async def action(self, kind, data, step=None, historical=False):
        if not self.state:
            raise ValueError("event without authenticated match")
        if kind == "ActionNewRound":
            signature = {k: data[k] for k in ("chang", "ju", "ben", "md5", "sha256", "saltSha256", "tiles", "scores", "liqibang") if k in data}
            self.round_key = digest(packed(signature))
        key = self.round_key or "pre_round"
        # Action step is scoped to the stable small round. End notifications have no step.
        identity = f"{self.state.match_id}:{key}:{step}:{kind}"
        if step is None:
            identity += ":" + hashlib.sha256(packed(data).encode()).hexdigest()
        event_id = digest(identity)
        existing = self.store.event(event_id)
        if existing and not historical:
            return self.state
        event = existing or Event(id=event_id, match_id=self.state.match_id, kind=kind,
                                  seq=self.store.next_seq(self.state.match_id), source_step=step, data=data,
                                  received_at=utcnow(), received_mono=time.monotonic(), historical=historical)
        if historical:
            event = event.model_copy(update={"historical": True})
        try:
            next_state = apply_event(self.state, event)
        except (ValueError, KeyError, TypeError) as error:
            self.store.record("reducer_failure", {"kind": kind, "event_id": event_id, "error_type": type(error).__name__}, self.state.match_id, self.state.round_uid)
            await self.gap("reducer_" + type(error).__name__)
            return self.state
        next_state.context["source_round_key"] = self.round_key
        if kind == "ActionNewRound":
            row = self.store.db.execute("SELECT seq FROM rounds WHERE id=?", (next_state.round_uid,)).fetchone()
            if row:
                next_state.round_seq = row[0]
        next_state.state_version = max(next_state.state_version, event.seq)
        if next_state.operation:
            next_state.operation.state_version = next_state.state_version
        self.state = next_state
        if not existing:
            self.store.save_event(event, next_state)
        if not historical:
            await self.changed(event)
        return next_state

    async def restore(self, restore):
        actions = restore.get("actions", [])
        if actions and actions[0].get("name") == "ActionNewRound":
            old = self.state
            self.state = Snapshot(match_id=old.match_id, self_seat=old.self_seat, rules=old.rules,
                                  round_seq=max(0, old.round_seq - 1))
            for action in actions:
                await self.action(action["name"], action["data"], action.get("step"), historical=True)
        elif actions and self.state.completeness == "full":
            for action in actions:
                if not self._known_action(action):
                    await self.action(action["name"], action["data"], action.get("step"), historical=True)
        else:
            snap = restore.get("snapshot", {})
            # Snapshot-only restores lack discard order, red/call metadata and event yaku.
            # Preserve them honestly; resume active input after complete action recovery/new round.
            self.state.hand = snap.get("hands", self.state.hand)
            self.state.scores = [p["score"] for p in snap["players"]] if len(snap.get("players", [])) == 4 else self.state.scores
            self.state.wind, self.state.hand_number = snap.get("chang", self.state.wind), snap.get("ju", self.state.hand_number - 1) + 1
            self.state.honba = snap.get("ben", self.state.honba)
            self.state.dora = snap.get("doras", self.state.dora)
            self.state.unknown_fields = ["snapshot_only_public_order", "snapshot_meld_semantics", "event_yaku_context"]
            self.state.completeness, self.state.consistent = "partial", False
        self.state.operation = None  # recovered timer age is not calibrated
        if self.state.lifecycle != "ended":
            self.state.lifecycle = "active"
        self.state.observed_at = utcnow()
        self.store.save_snapshot(self.state)
        self.store.record("recovery", {"actions": len(actions), "snapshot_only": not bool(actions),
                                       "passed_waiting_time_raw": restore.get("passed_waiting_time"), "opened_operation": False}, self.state.match_id, self.state.round_uid)
        await self.changed(None)

    def _known_action(self, action):
        identity = f"{self.state.match_id}:{self.round_key or 'pre_round'}:{action.get('step')}:{action['name']}"
        return self.store.has_event(digest(identity))

    async def interrupt(self, reason="service_stopped"):
        if self.state and self.state.lifecycle == "active":
            self.state = self.state.model_copy(deep=True)
            self.state.lifecycle, self.state.phase, self.state.operation = "interrupted", Phase.INTERRUPTED, None
            self.store.save_snapshot(self.state)
            self.store.record("interruption", {"reason": reason}, self.state.match_id, self.state.round_uid)
            await self.changed(None)
