"""One non-queuing input owner shared by navigation and gameplay."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from .native import InputDeferred


class InputBusy(InputDeferred):
    pass


class InputLease:
    def __init__(self):
        self.owner: str | None = None
        self._ticket = None

    @asynccontextmanager
    async def hold(self, owner: str):
        # No await between checking and acquiring: all callers run on one loop.
        # Waiting for an old owner would make a timed gameplay target stale.
        if self.owner is not None:
            raise InputBusy("input already owned")
        ticket = object()
        self.owner, self._ticket = owner, ticket
        try:
            yield
        finally:
            if self._ticket is ticket:
                self.owner, self._ticket = None, None


async def finish_input(awaitable):
    """Cancellation must not release a lease while a native input thread runs."""
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Stop flags are set by the caller first. Wait for any already-sent
        # mouse press/release to settle before another owner can acquire input.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()  # Consume a possible input failure during shutdown.
        raise
