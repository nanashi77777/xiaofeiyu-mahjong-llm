"""Control-only visible room management for authorized solo practice tests."""
from __future__ import annotations

import asyncio
import time
from typing import Literal

from pydantic import Field, StrictBool, StrictStr

from .domain import Record
from .lobby import LobbyError


class RoomAction(Record):
    command_id: StrictStr = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    room_code: StrictStr = Field(pattern=r"^[0-9]{1,12}$")
    action: Literal["leave", "confirm_leave", "add_robot_0", "add_robot_1", "add_robot_2", "add_robot_3", "start"]
    takeover: StrictBool = False


class PracticeRooms:
    ACK_TIMEOUT = 12

    def __init__(self, service):
        self.service = service

    async def run(self, command):
        service = self.service
        store, session = service.store, service.engine.lobby_session
        request = command.model_dump()
        previous = next((r["payload"] for r in store.records("room_command", limit=1000)
                         if r["payload"]["request"]["command_id"] == command.command_id), None)
        if previous:
            if previous["request"] != request:
                raise LobbyError("command_id_content_conflict")
            return previous
        generation = session.generation
        old_match = service.engine.state.match_id if service.engine.state else None

        class Context:
            def check(self):
                state = service.engine.state
                if service.coordinator.enabled or state and state.lifecycle == "active":
                    raise LobbyError("active_game_requires_handoff")
                if service.executor.mouse.stop.is_set():
                    raise LobbyError("navigation_stopped")
                if not (session.authenticated and session.generation == generation and session.room
                        and session.room["room_code"] == command.room_code and not session.room.get("playing")):
                    raise LobbyError("practice_room_changed")
                solo_ai = session.room.get("players") == 1 and session.room.get("mode", {}).get("mode") == 3
                if command.action == "start" and not (session.room.get("humans") == 1 and
                                                       (solo_ai or session.room.get("robots") == 3)):
                    raise LobbyError("practice_requires_one_human_three_robots")

            def before_submit(self, step):
                self.check()
                store.record("room_command", {"request":request, "status":"submitted", "method":step})

        async with service.input_lease.hold("room:"+command.command_id):
            if service.navigator.busy():
                raise LobbyError("input_busy")
            service.executor.mouse.stop.clear()
            ctx = Context()
            ctx.check()
            scene = "confirm_leave" if command.action == "confirm_leave" else "room"
            control = {"leave":"back", "confirm_leave":"confirm"}.get(command.action, command.action)
            method = ("leaveRoom" if command.action == "confirm_leave" else "startRoom" if command.action == "start"
                      else "addRoomRobot" if command.action.startswith("add_robot") else None)
            before = time.monotonic()
            await service.lobby_ui.click(scene, control, ctx, submit=method)
            if method is None:
                result = {"request":request, "status":"dialog_opened"}
            else:
                deadline = time.monotonic()+self.ACK_TIMEOUT
                ack = None
                while time.monotonic() < deadline:
                    ack = session.latest.get(method)
                    if ack and ack["observed_mono"] >= before:
                        break
                    if service.executor.mouse.stop.is_set():
                        raise LobbyError("navigation_stopped")
                    await asyncio.sleep(.05)
                if not ack or ack["observed_mono"] < before:
                    result = {"request":request, "status":"uncertain", "method":method,
                              "reason":"server_confirmation_not_observed"}
                else:
                    result = {"request":request, "status":"confirmed" if not ack["error_code"] else "rejected",
                              "error_code":ack["error_code"]}
            store.record("room_command", result)
        if command.action == 'start' and command.takeover and result['status'] == 'confirmed':
            deadline = time.monotonic()+35
            while time.monotonic() < deadline:
                state = service.engine.state
                if (state and state.match_id != old_match and state.lifecycle == 'active'
                        and state.completeness == 'full' and state.consistent and state.round_uid
                        and service.engine.authenticated_mono >= before
                        and session.generation == generation):
                    await service.executor.bind_dedicated()
                    await service.coordinator.start(shadow=False)
                    result = {**result,'takeover':'active','match_id':state.match_id}
                    store.record('room_command',result)
                    return result
                if service.executor.mouse.stop.is_set():
                    break
                await asyncio.sleep(.05)
            result = {**result,'takeover':'waiting_for_fresh_game'}
            store.record('room_command',result)
        return result
