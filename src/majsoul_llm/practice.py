"""Control-only visible room management for authorized solo practice tests."""
from __future__ import annotations

import asyncio
import time
from typing import Literal

from pydantic import Field, StrictBool, StrictStr

from .domain import Record
from .lobby import LobbyError, WaitingUser


class RoomAction(Record):
    command_id: StrictStr = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    room_code: StrictStr = Field(pattern=r"^[0-9]{1,12}$")
    action: Literal["leave", "confirm_leave", "add_robot_0", "add_robot_1", "add_robot_2", "add_robot_3", "start", "ready", "unready"]
    takeover: StrictBool = False


class PracticeRooms:
    ACK_TIMEOUT = 12

    def __init__(self, service, *, solo_only=True):
        self.service = service
        self.solo_only = solo_only

    async def run(self, command):
        store=self.service.store
        previous=next((r['payload'] for r in store.records('room_command',limit=1000)
                       if r['payload']['request']['command_id']==command.command_id),None)
        if previous:
            if previous['request']!=command.model_dump():
                raise LobbyError('command_id_content_conflict')
            return previous
        operations=getattr(self.service,'operations',None)
        authorization=None
        try:
            if command.action=='start' and (command.takeover or operations and operations.arm_id):
                if not operations:
                    raise LobbyError('prearmed_takeover_adapter_required')
                authorization=operations.arm_id
                authorization=await operations.prepare_room_start(command)
            result=await self._run(command,authorization)
            if authorization:
                result=await operations.room_start_result(authorization,result)
                store.record('room_command',result)
            return result
        except BaseException:
            if authorization:
                await operations.cancel_room_start(authorization,'room_start_failed_or_stopped')
            raise

    async def _run(self, command, authorization):
        service = self.service
        store, session = service.store, service.engine.lobby_session
        request = command.model_dump()
        generation = session.generation
        solo_only = self.solo_only

        class Context:
            def check(self):
                state = service.engine.state
                if service.coordinator.enabled or state and state.lifecycle == "active":
                    raise LobbyError("active_game_requires_handoff")
                if service.executor.mouse.stop.is_set():
                    raise LobbyError("navigation_stopped")
                if authorization and (service.operations.arm_id!=authorization or service.operations.arm_task.done()):
                    raise LobbyError('autoplay_not_prepared_before_room_start')
                if not (session.authenticated and session.generation == generation and session.room
                        and session.room["room_code"] == command.room_code and not session.room.get("playing")):
                    raise LobbyError("practice_room_changed")
                solo_ai = session.room.get("players") == 1 and session.room.get("mode", {}).get("mode") == 3
                if solo_only and command.action == "start" and not (session.room.get("humans") == 1 and
                                                       (solo_ai or session.room.get("robots") == 3)):
                    raise LobbyError("practice_requires_one_human_three_robots")

            def before_submit(self, step):
                self.check()
                store.record("room_command", {"request":request, "status":"submitted", "method":step})

        async with service.input_lease.hold("room:"+command.command_id):
            if service.navigator.busy():
                raise LobbyError("input_busy")
            if not authorization:
                service.executor.mouse.stop.clear()
            ctx = Context()
            ctx.check()
            if command.action in ('ready','unready') and session.prepared is (command.action == 'ready'):
                result={'request':request,'status':'confirmed','already_in_requested_state':True,
                        'prepared':session.prepared}
                store.record('room_command',result)
                return result
            scene = "confirm_leave" if command.action == "confirm_leave" else "room"
            control = {"leave":"back", "confirm_leave":"confirm"}.get(command.action, command.action)
            method = ("leaveRoom" if command.action in ("leave","confirm_leave") else "startRoom" if command.action == "start"
                      else "addRoomRobot" if command.action.startswith("add_robot")
                      else "readyPlay" if command.action in ('ready','unready') else None)
            before = time.monotonic()
            await service.lobby_ui.click(scene, control, ctx, submit=method)
            if command.action!='start':
                return await self._observe_result(command,request,session,method,before)
        # The first gameplay event may precede the start response. Do not hold
        # the UI lease while waiting for that response and block the prearmed AI.
        return await self._observe_result(command,request,session,method,before)

    async def _observe_result(self, command, request, session, method, before):
        service=self.service
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
                if command.action == 'leave':
                    try:
                        if await service.lobby_ui.observe() == 'confirm_leave':
                            break
                    except WaitingUser:
                        pass
                await asyncio.sleep(.05)
            if not ack or ack["observed_mono"] < before:
                scene = None
                if command.action == 'leave':
                    try:
                        scene = await service.lobby_ui.observe()
                    except WaitingUser:
                        pass
                result = ({"request":request, "status":"dialog_opened", "method":method}
                          if scene == 'confirm_leave' else
                          {"request":request, "status":"uncertain", "method":method,
                           "reason":"server_confirmation_not_observed"})
            else:
                result = {"request":request, "status":"confirmed" if not ack["error_code"] else "rejected",
                          "error_code":ack["error_code"]}
                if command.action in ('ready','unready') and not ack['error_code']:
                    expected=command.action == 'ready'
                    result.update(status='confirmed' if ack.get('ready') is expected else 'uncertain',
                                  prepared=ack.get('ready'))
        service.store.record("room_command", result)
        return result
