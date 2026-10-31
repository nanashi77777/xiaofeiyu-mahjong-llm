"""Ephemeral, allowlisted session/room evidence. No login identity is retained."""
from __future__ import annotations

import time

LOGIN_METHODS = {"login", "emailLogin", "oauth2Login", "fastLogin", "loginSuccess", "loginBeat"}
LOBBY_METHODS = LOGIN_METHODS | {"fetchRoom", "createRoom", "joinRoom", "readyPlay", "leaveRoom", "matchGame", "cancelMatch",
                                "addRoomRobot", "startRoom"}
ROOM_NOTIFICATIONS = {"NotifyRoomPlayerUpdate", "NotifyRoomGameStart", "NotifyRoomKickOut"}


def room_facts(room):
    """Project an already sanitized room again before durable confirmation."""
    mode = room.get("mode", {})
    detail = mode.get("detail_rule", {})
    return {**{key:room.get(key) for key in ("room_code", "players", "playing", "robots")},
            **({"humans":room["humans"]} if "humans" in room else {}),
            "mode":{**{key:mode[key] for key in ("mode", "ai") if key in mode},
                    "detail_rule":{key:detail[key] for key in ("time_fixed", "time_add") if key in detail}}}


def sanitize_lobby(method, data):
    result = {"error_code":data.get("error", {}).get("code", 0)}
    if method in LOGIN_METHODS:
        result["has_game"] = bool(data.get("game_info", {}).get("game_uuid"))
    room = data.get("room") or {}
    if room.get("room_id"):
        # IDs and the exact room mode are local navigation evidence. No player
        # identity, access token, address or account information is returned.
        result["room"] = room_facts({"room_code":str(room["room_id"]), "players":room.get("max_player_count"),
                                     "humans":len(room["persons"]) if "persons" in room else None,
                                     "playing":room.get("is_playing", False),
                                     "robots":len(room.get("robots", [])) or room.get("robot_count", 0),
                                     "mode":room.get("mode", {})})
    return result


def sanitize_room_notification(method, data):
    if method == "NotifyRoomPlayerUpdate":
        # Current client populates robots[] while legacy robot_count stays 0.
        # Keep counts only; robot appearance and human identities are discarded.
        return {"humans":len(data.get("player_list", [])),
                "robots":len(data.get("robots", [])) or data.get("robot_count", 0)}
    # Start notifications carry connection credentials; none are forwarded.
    return {}


class LobbySession:
    def __init__(self):
        self.reset()

    def reset(self):
        self.authenticated = False
        self.connection = None
        self.observed_mono = 0
        self.has_game = False
        self.room = None
        self.prepared = None
        self.matching = False
        self.latest = {}
        self.generation = getattr(self, "generation", 0) + 1

    def closed(self, connection):
        if self.connection == connection:
            self.reset()

    def observe(self, message):
        method = message["method"].rsplit(".", 1)[-1]
        data = message["data"]
        if method in ROOM_NOTIFICATIONS:
            if message["connection"] != self.connection:
                return
            if self.room and method == "NotifyRoomPlayerUpdate":
                self.room.update(data)
            elif self.room and method == "NotifyRoomGameStart":
                self.room["playing"] = True
                self.prepared = None
                self.has_game = True
            elif method == "NotifyRoomKickOut":
                self.room, self.prepared = None, None
            self.latest[method] = {**data, "observed_mono":time.monotonic()}
            return
        self.latest[method] = {**data, "observed_mono":time.monotonic()}
        if method in LOGIN_METHODS:
            self.authenticated = not bool(data["error_code"])
            self.connection = message["connection"]
            self.observed_mono = time.monotonic()
            # loginBeat/loginSuccess do not carry game_info; don't clear a
            # pending reconnect signalled by the preceding full login response.
            if method not in ("loginBeat", "loginSuccess"):
                self.has_game = data.get("has_game", False)
        if data.get("room") and not data["error_code"]:
            self.room = data["room"]
            self.prepared = None
        if method == "fetchRoom" and not data["error_code"] and not data.get("room"):
            self.room = None
            self.prepared = None
        if method == "leaveRoom" and not data["error_code"]:
            self.room = None
            self.prepared = None
        if method == "readyPlay" and not data["error_code"] and self.room:
            self.prepared = data.get("ready")
        if method in ("matchGame", "cancelMatch") and not data["error_code"]:
            self.matching = method == "matchGame"

    def public(self):
        return {"authenticated":self.authenticated, "has_game":self.has_game,
                "has_room":bool(self.room), "prepared":self.prepared, "matching":self.matching, "generation":self.generation,
                "room":room_facts(self.room) if self.room else None,
                "age_seconds":round(time.monotonic()-self.observed_mono, 1) if self.observed_mono else None}
