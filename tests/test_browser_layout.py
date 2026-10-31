from types import SimpleNamespace

import pytest

from majsoul_llm.browser import Browser
from majsoul_llm.config import Settings
from majsoul_llm.native import InputDeferred


@pytest.mark.parametrize("bounds", [
    {"x":0,"y":0,"width":1280,"height":720,"right":1280,"bottom":720},
    {"x":0,"y":-30,"width":900,"height":506,"right":900,"bottom":476},
    {"x":0,"y":0,"width":0,"height":0,"right":0,"bottom":0},
])
async def test_clipped_canvas_is_rejected_before_screenshot_can_scroll(bounds):
    captures = []
    async def count():
        return 1
    async def screenshot(**kwargs):
        captures.append(kwargs)
        return b"unexpected"
    async def evaluate(script):
        return {"width":1000,"height":600,"canvas":bounds}
    browser = Browser(Settings(), None)
    browser.page = SimpleNamespace(locator=lambda _:SimpleNamespace(count=count,screenshot=screenshot),evaluate=evaluate)
    with pytest.raises(InputDeferred, match="canvas is clipped"):
        await browser.image_bytes()
    assert not captures


async def test_fitting_own_window_restores_minimized_then_maximizes():
    commands = []
    async def send(method, params=None):
        commands.append((method,params))
        if method == "Browser.getWindowForTarget":
            return {"windowId":77,"bounds":{"windowState":"minimized" if len(commands)==1 else "maximized"}}
    browser = Browser(Settings(), None)
    browser.page = object()
    browser.cdp = SimpleNamespace(send=send)
    assert (await browser.fit_window())["windowState"] == "maximized"
    states = [p["bounds"]["windowState"] for method,p in commands if method == "Browser.setWindowBounds"]
    assert states == ["normal", "maximized"]
