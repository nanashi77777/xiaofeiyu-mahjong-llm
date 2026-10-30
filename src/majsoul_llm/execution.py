"""Semantic target resolution and compound physical-input transaction."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .domain import LegalAction, Snapshot
from .native import InputDeferred, Window, WindowsMouse
from .locator import ScreenLocator

BUTTONS = {"riichi": ("立直", "btn_lizhi", "btn_liqi"), "chi": ("吃", "btn_chi"), "pon": ("碰", "btn_peng"),
           "daiminkan": ("杠", "btn_gang"), "ankan": ("杠", "btn_gang"), "kakan": ("杠", "btn_gang"),
           "tsumo": ("自摸", "btn_zimo"), "ron": ("和", "荣和", "btn_hu", "btn_ron"),
           "pass": ("跳过", "取消", "btn_cancel", "btn_pass"), "abort9": ("九种九牌",)}


@dataclass
class Target:
    point: tuple[int, int]
    rect: tuple[int, int, int, int]
    observed: float
    evidence: str
    page_point: tuple[float,float] | None = None


class Executor:
    def __init__(self, browser, state_getter):
        self.browser, self.state_getter = browser, state_getter
        self.mouse = WindowsMouse()
        self.window: Window | None = None
        self.calibration = None
        self.locator = ScreenLocator(browser.settings.runtime)
        self.input_mode = browser.settings.input_mode

    async def bind(self, hwnd):
        matches = [w for w in self.mouse.windows() if w.hwnd == hwnd]
        if len(matches) != 1:
            raise ValueError("game window not found")
        self.window = matches[0]
        self.mouse.stop.clear()

    async def target(self, semantic, state, indices=None):
        if not self.window:
            raise InputDeferred("no window bound")
        if self.input_mode=='browser_background':
            # Clear a stale hovered/lifted tile without touching the OS cursor.
            await self.browser.prepare_background()
            await self.browser.page.mouse.move(5,5)
            await asyncio.sleep(.05)
        if self.input_mode=='browser_background':
            from ctypes import wintypes
            import ctypes
            pid=wintypes.DWORD()
            self.mouse.user.GetWindowThreadProcessId(self.window.hwnd,ctypes.byref(pid))
            if not self.mouse.user.IsWindow(self.window.hwnd) or pid.value!=self.window.pid:
                raise InputDeferred('bound browser window was closed')
            rect=(0,0,0,0)
        else:
            rect = self.mouse.rect(self.window)
        nodes = await self.browser.visible_nodes()
        observed = time.monotonic()
        aliases = BUTTONS.get(semantic, ())
        found = [n for n in nodes if n["text"].strip() in aliases or n["name"] in aliases]
        if semantic == "tile":
            # Exact, visible tile nodes may be supplied by a validated UI adapter.
            found = [n for n in nodes if n.get("name") == f"hand_tile_{indices[0]}" and n.get("text") == state.hand[indices[0]]]
        if len(found) == 1:
            node = found[0]
            evidence = "visible_ui_semantics"
        else:
            raw = await self.browser.image_bytes()
            observed = time.monotonic()
            if semantic == "tile":
                box = await asyncio.to_thread(self.locator.hand,raw,state)
                box = box[indices[0]]
            elif semantic.startswith("combo:"):
                kind = semantic.split(":",1)[1]
                tiles = [state.hand[i] for i in indices]
                box = await asyncio.to_thread(self.locator.combo,raw,kind,tiles)
            else:
                box = await asyncio.to_thread(self.locator.button,raw,semantic)
            node = {"x":box.center[0],"y":box.center[1]}
            evidence = "calibrated_screenshot_matching"
        geometry = await self.browser.page.evaluate("""() => ({x:window.screenX+(window.outerWidth-window.innerWidth)/2,
          y:window.screenY+window.outerHeight-window.innerHeight,ratio:window.devicePixelRatio,
          canvas:document.querySelector('#unity-canvas')?.getBoundingClientRect().toJSON()})""")
        canvas=geometry.get("canvas") if evidence=="calibrated_screenshot_matching" else None
        x = round((geometry["x"] + node["x"] + (canvas["x"] if canvas else 0)) * geometry["ratio"])
        y = round((geometry["y"] + node["y"] + (canvas["y"] if canvas else 0)) * geometry["ratio"])
        page_point=(node['x']+(canvas['x'] if canvas else 0),node['y']+(canvas['y'] if canvas else 0))
        return Target((x, y), rect, observed, evidence,page_point)

    def _current(self, state, action):
        current = self.state_getter()
        if not current or not current.operation or current.operation.window_id != action.window_id:
            raise InputDeferred("operation no longer current")
        if current.state_version != state.state_version:
            raise InputDeferred("state changed before input")

    async def execute(self, state: Snapshot, action: LegalAction, deadline):
        self._current(state, action)

        async def click(semantic, indices=None):
            target = await self.target(semantic, state, indices)
            self._current(state, action)
            if self.input_mode=='browser_background':
                if self.mouse.stop.is_set() or self.mouse.user.GetAsyncKeyState(0x1B)&0x8000:
                    self.mouse.stop.set()
                    raise InputDeferred('input stopped')
                if time.monotonic()-target.observed>1.2:
                    raise InputDeferred('background layout expired')
                return await self.browser.background_click(target.page_point,deadline,self.mouse.stop,
                                                           validate=lambda:self._current(state,action))
            return await asyncio.to_thread(self.mouse.click, self.window, target.rect, target.point, target.observed, deadline)

        if action.kind == "discard":
            return await click("tile", [action.tile_index])
        if action.kind == "riichi":
            await click("riichi")
            # UI selection is independent of the authoritative state version.
            for _ in range(15):
                if time.monotonic() >= deadline:
                    raise TimeoutError("riichi selection expired")
                try:
                    return await click("tile", [action.tile_index])
                except InputDeferred:
                    await asyncio.sleep(0.02)
            raise InputDeferred("riichi selection not verified")
        if action.kind in ("chi", "pon", "daiminkan", "ankan", "kakan"):
            # A combination crop is required before opening the menu, so an
            # unsupported menu never strands the UI after the first click.
            profile = self.locator.profile()
            tiles = [state.hand[i] for i in action.consume_indices]
            label = "combo_"+action.kind+"_"+"_".join(sorted(tiles))
            if label not in profile["templates"]:
                raise InputDeferred("selected meld combination needs calibration")
            await click(action.kind)
            for _ in range(20):
                if time.monotonic() >= deadline:
                    raise TimeoutError("meld menu expired")
                try:
                    return await click("combo:"+action.kind,action.consume_indices)
                except InputDeferred:
                    await asyncio.sleep(.02)
            raise InputDeferred("chosen meld combination not visible")
        return await click(action.kind)
