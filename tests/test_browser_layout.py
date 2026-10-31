from types import SimpleNamespace
import asyncio
import base64
import json

import cv2
import numpy as np
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


def test_native_dpi_viewport_is_cropped_locally_at_canvas_offset():
    image = np.zeros((900,1500,3),dtype=np.uint8)
    image[150:750,225:1275] = (20,130,240)
    raw = cv2.imencode('.png',image)[1].tobytes()
    layout = {"width":1000,"height":600,"canvas":{
        "x":150,"y":100,"right":850,"bottom":500,"width":700,"height":400}}
    cropped = cv2.imdecode(np.frombuffer(Browser.crop_viewport(raw,layout),dtype=np.uint8),cv2.IMREAD_COLOR)
    assert cropped.shape == (400,700,3)
    assert np.all(cropped == (20,130,240))
    with pytest.raises(InputDeferred,match='scale mismatch'):
        Browser.crop_viewport(raw,{**layout,"width":800})


async def test_capture_is_serial_and_never_requests_render_scale_or_device_override():
    commands, active, peak = [], 0, 0
    layout = {"width":100,"height":60,"canvas":None}
    png = cv2.imencode('.png',np.zeros((90,150,3),dtype=np.uint8))[1].tobytes()
    async def send(method,params):
        nonlocal active, peak
        active += 1
        peak = max(peak,active)
        commands.append((method,params))
        await asyncio.sleep(.01)
        active -= 1
        return {"data":base64.b64encode(png).decode()}
    async def evaluate(script):
        return layout
    browser = Browser(Settings(),None)
    browser.page = SimpleNamespace(evaluate=evaluate)
    browser.cdp = SimpleNamespace(send=send)
    await asyncio.gather(*(browser.image_bytes() for _ in range(3)))
    assert peak == 1 and len(commands) == 3
    assert all(method == 'Page.captureScreenshot' and params == {
        'format':'jpeg','quality':90,'fromSurface':True,'captureBeyondViewport':False} for method,params in commands)


async def test_resize_during_native_capture_cannot_become_an_input_image():
    layouts = iter([{"width":100,"height":60,"canvas":None},{"width":200,"height":120,"canvas":None}])
    async def evaluate(script):
        return next(layouts)
    async def send(method,params):
        return {"data":""}
    browser = Browser(Settings(),None)
    browser.page = SimpleNamespace(evaluate=evaluate)
    browser.cdp = SimpleNamespace(send=send)
    with pytest.raises(InputDeferred,match='layout changed during capture'):
        await browser.image_bytes()


def test_reattach_rejects_another_profile_or_executable(monkeypatch, tmp_path):
    from majsoul_llm.native import windows_command_args
    runtime = tmp_path/'profile with spaces'
    b = Browser(Settings(runtime=runtime), None)
    command = f'"{b.settings.chrome_path}" --user-data-dir="{runtime / "browser-profile"}" --remote-debugging-port=9223'
    assert windows_command_args(command)[1] == '--user-data-dir='+str(runtime/'browser-profile')
    payload = {'ExecutablePath':b.settings.chrome_path,'CommandLine':command}
    def run(*args,**kwargs):
        return SimpleNamespace(stdout=json.dumps(payload))
    monkeypatch.setattr('majsoul_llm.browser.subprocess.run',run)
    b._verify_profile_process(12)
    payload['CommandLine'] = 'chrome.exe --user-data-dir="C:\\unrelated profile"'
    with pytest.raises(InputDeferred,match='profile differs'):
        b._verify_profile_process(12)
    payload['ExecutablePath'] = str(tmp_path/'other.exe')
    with pytest.raises(InputDeferred,match='executable differs'):
        b._verify_profile_process(12)
