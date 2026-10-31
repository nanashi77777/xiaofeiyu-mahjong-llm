"""Visible result-page confirmations, gated by authoritative round/game ends."""
from __future__ import annotations

import asyncio
import time
import hashlib

from .lobby import LobbyError, WaitingUser


class RoundFlow:
    def __init__(self, service):
        self.service = service
        self.task = None
        self.round_uid = None
        self.last_scene = None

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def changed(self, state, allowed):
        if self.task and not self.task.done():
            if state and state.round_uid == self.round_uid and state.phase.value in ('round_end','ended'):
                return
            self.task.cancel()
        if state and allowed and state.phase.value in ('round_end','ended'):
            self.round_uid = state.round_uid
            self.task = asyncio.create_task(self.confirm_pages(state.match_id,state.round_uid))

    async def confirm_pages(self, match_id, round_uid):
        service = self.service
        deadline = time.monotonic()+45

        class Context:
            def check(self):
                state = service.engine.state
                if service.executor.mouse.stop.is_set() or not state or state.match_id != match_id or state.round_uid != round_uid:
                    raise LobbyError('result_flow_superseded')
                if state.phase.value not in ('round_end','ended'):
                    raise LobbyError('result_flow_new_round')

            def before_submit(self, step):
                self.check()

        ctx = Context()
        clicked = set()
        try:
            while time.monotonic() < deadline:
                ctx.check()
                try:
                    scene = await service.lobby_ui.observe()
                    self.last_scene = scene
                except WaitingUser:
                    await asyncio.sleep(.2)
                    continue
                if scene in ('room','lobby'):
                    return
                if scene not in ('round_win','round_score','final') or scene in clicked:
                    await asyncio.sleep(.2)
                    continue
                async with service.input_lease.hold('result:'+round_uid):
                    ctx.check()
                    clicked.add(scene)
                    folder = service.settings.runtime / 'results'
                    folder.mkdir(parents=True,exist_ok=True)
                    filename = hashlib.sha256((match_id+round_uid+scene).encode()).hexdigest()[:24]+'.png'
                    (folder/filename).write_bytes(await service.browser.image_bytes())
                    service.store.record('result_ui',{'scene':scene,'status':'submitted','screenshot':filename},match_id,round_uid)
                    try:
                        await service.lobby_ui.click(scene,'confirm',ctx)
                    except TimeoutError:
                        # A scene transition can suspend Unity's release-frame
                        # callback after the click. Do not press a second time.
                        service.store.record('result_ui',{'scene':scene,'status':'awaiting_scene_after_release'},match_id,round_uid)
                await asyncio.sleep(.2)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            service.store.record('result_ui',{'status':'deferred','error_type':type(error).__name__},match_id,round_uid)
