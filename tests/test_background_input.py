import threading
import time
from types import SimpleNamespace

import pytest

from majsoul_llm.browser import Browser
from majsoul_llm.config import Settings


class Session:
    def __init__(self):
        self.commands = []
        self.detached = False

    async def send(self, method, params=None):
        self.commands.append((method, params))

    def on(self, *args):
        pass

    async def detach(self):
        self.detached = True


class Mouse:
    def __init__(self):
        self.events = []

    async def move(self, *point):
        self.events.append(("move", point))

    async def down(self):
        self.events.append(("down",))

    async def up(self):
        self.events.append(("up",))


def browser():
    session, mouse = Session(), Mouse()
    async def new_session(page):
        return session
    async def evaluate(script):
        return {"x":5,"y":5} if "__mjLocalPointer" in script else True
    b = Browser(Settings(), None)
    b.page = SimpleNamespace(url=b.settings.game_url, mouse=mouse, evaluate=evaluate,
                             context=SimpleNamespace(new_cdp_session=new_session), is_closed=lambda:False)
    return b, session, mouse


async def test_background_frame_wait_cannot_click_superseded_state():
    b, session, mouse = browser()
    def stale():
        raise ValueError("operation changed while awaiting renderer")
    with pytest.raises(ValueError, match="operation changed"):
        await b.background_click((30,40), time.monotonic()+2, threading.Event(), validate=stale)
    assert not any(e[0]=="down" for e in mouse.events)
    assert any(cmd=="Page.startScreencast" for cmd,_ in session.commands)
    assert b.input_cdp is session
    await b.release_background()
    assert session.detached and b.input_cdp is None


async def test_background_expired_or_stopped_input_never_reaches_mouse():
    b, session, mouse = browser()
    stopped = threading.Event()
    stopped.set()
    for deadline, flag in ((time.monotonic()-1, threading.Event()), (time.monotonic()+2, stopped)):
        with pytest.raises(TimeoutError):
            await b.background_click((30,40), deadline, flag)
    assert not session.commands and not mouse.events
