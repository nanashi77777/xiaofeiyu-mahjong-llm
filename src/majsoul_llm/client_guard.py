"""Recover the visible AFK notice without treating automatic play as our input."""
from __future__ import annotations

import asyncio
import time

from .lobby import LobbyError, WaitingUser


class ClientGuard:
    def __init__(self, service):
        self.service = service
        self.task = None
        self.next_probe = 0

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def changed(self, state, allowed):
        if not allowed:
            # Our own pause lets the UI release finish when a real event arrives.
            # A user stop changes status/generation and still cancels recovery.
            if self.service.coordinator.status == 'client_away_notice':
                return
            await self.stop()
            return
        c = self.service.coordinator
        if (not state or state.lifecycle != 'active' or state.operation or c.active()
                or self.task and not self.task.done() or time.monotonic() < self.next_probe):
            return
        self.next_probe = time.monotonic()+3
        self.task = asyncio.create_task(self.check(state.match_id,c.generation))

    async def check(self, match_id, generation):
        s, resumed = self.service, False
        c = s.coordinator
        try:
            if await s.lobby_ui.observe() != 'away':
                return
            if c.generation != generation or not c.enabled or s.engine.state.match_id != match_id:
                return
            s.store.record('client_away',{'status':'observed','source':'visible_calibrated_dialog'},
                           match_id,s.engine.state.round_uid)
            await c.stop('client_away_notice')
            paused_generation = c.generation
            if paused_generation != generation+1:
                return
            s.executor.mouse.stop.clear()

            class Context:
                def check(self):
                    state = s.engine.state
                    if (c.generation != paused_generation or s.executor.mouse.stop.is_set()
                            or not state or state.match_id != match_id or state.lifecycle != 'active'):
                        raise LobbyError('client_recovery_superseded')

                def before_submit(self, step):
                    self.check()

            async with s.input_lease.hold('client-away:'+match_id):
                await s.lobby_ui.click('away','resume',Context())
            resumed = c.generation == paused_generation and not s.executor.mouse.stop.is_set()
            if resumed:
                s.store.record('client_away',{'status':'resume_clicked','source':'visible_ui'},
                               match_id,s.engine.state.round_uid)
                await c.start(shadow=False)
        except WaitingUser:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            s.store.record('client_away',{'status':'recovery_deferred','error_type':type(error).__name__},match_id)
