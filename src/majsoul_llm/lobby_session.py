"""Ephemeral, allowlisted session/room evidence. No login identity is retained."""
from __future__ import annotations

import time

LOGIN_METHODS = {"login", "emailLogin", "oauth2Login", "fastLogin", "loginSuccess", "loginBeat"}
LOBBY_METHODS = LOGIN_METHODS | {"fetchRoom", "createRoom", "joinRoom", "leaveRoom", "matchGame", "cancelMatch"}


def sanitize_lobby(method, data):
    result = {"error_code":data.get("error", {}).get("code", 0)}
    if method in LOGIN_METHODS:
        result["has_game"] = bool(data.get("game_info", {}).get("game_uuid"))
    room = data.get("room") or {}
    if room.get("room_id"):
        # IDs and the exact room mode are local navigation evidence. No player
        # identity, access token, address or account information is returned.
        result["room"] = {"room_code":str(room["room_id"]), "players":room.get("max_player_count"),
                          "playing":room.get("is_playing", False), "robots":room.get("robot_count", 0),
                          "mode":room.get("mode", {})}
    return result


class LobbySession:
    def __init__(self):
        self.reset()

    def reset(self):
        self.authenticated = False
        self.connection = None
        self.observed_mono = 0
        self.has_game = False
        self.room = None
        self.matching = False
        self.latest = {}
        self.generation = getattr(self, "generation", 0) + 1

    def closed(self, connection):
        if self.connection == connection:
            self.reset()

    def observe(self, message):
        method = message["method"].rsplit(".", 1)[-1]
        data = message["data"]
        self.latest[method] = {**data, "observed_mono":time.monotonic()}
        if method in LOGIN_METHODS:
            self.authenticated = not bool(data["error_code"])
            self.connection = message["connection"]
            self.observed_mono = time.monotonic()
            # loginBeat/loginSuccess do not carry game_info; don't clear a
            # pending reconnect signalled by the preceding full login response.
            if method not in ("loginBeat", "loginSuccess"):
                self.has_game = data.get("has_game", False)
        if data.get("room"):
            self.room = data["room"]
        if method == "leaveRoom" and not data["error_code"]:
            self.room = None
        if method in ("matchGame", "cancelMatch") and not data["error_code"]:
            self.matching = method == "matchGame"

    def public(self):
        return {"authenticated":self.authenticated, "has_game":self.has_game,
                "has_room":bool(self.room), "matching":self.matching, "generation":self.generation,
                "age_seconds":round(time.monotonic()-self.observed_mono, 1) if self.observed_mono else None}
