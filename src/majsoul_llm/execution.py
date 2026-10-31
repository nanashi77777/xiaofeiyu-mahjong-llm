"""Semantic target resolution and compound physical-input transaction."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass

from .domain import LegalAction, Snapshot
from .input_lease import InputLease, finish_input
from .native import InputDeferred, TargetExpired, Window, WindowsMouse
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
    layout: dict | None = None
    frame: bytes | None = None
    confidence: float | None = None


class Executor:
    def __init__(self, browser, state_getter, lease=None):
        self.browser, self.state_getter = browser, state_getter
        self.mouse = WindowsMouse()
        self.window: Window | None = None
        self.calibration = None
        self.locator = ScreenLocator(browser.settings.runtime)
        self.input_mode = browser.settings.input_mode
        self.lease = lease or InputLease()
        self.metrics = []
        self.observed_targets = {}
        self.idle_observation = None
        self.idle_hand_signature = None
        self.target_evidence = []
        self.offered_window = None
        self.offered_saved = set()

    async def cancel_idle_observation(self):
        task = self.idle_observation
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        self.idle_observation = None

    async def refresh_idle_hand(self,state,allowed):
        signature = self.locator.hand_signature(state) if state else None
        if (not allowed or not state or state.operation or state.lifecycle != 'active'
                or not state.consistent or state.phase.value not in ('waiting','awaiting_replacement')):
            await self.cancel_idle_observation()
            return
        if signature == self.idle_hand_signature:
            return
        await self.cancel_idle_observation()
        self.idle_observation = asyncio.create_task(self._observe_idle_hand(state,signature))

    async def _observe_idle_hand(self,state,signature):
        try:
            async with self.lease.hold('idle_hand:'+state.round_uid):
                current = self.state_getter()
                if self.mouse.stop.is_set() or not current or current.operation or self.locator.hand_signature(current) != signature:
                    return
                async def neutral():
                    await self.browser.prepare_background()
                    await self.browser.page.mouse.move(5,5)
                    await asyncio.sleep(.05)
                if self.input_mode != 'browser_background':
                    return
                await finish_input(neutral())
                raw = await self.browser.image_bytes()
                await finish_input(asyncio.to_thread(self.locator.hand,raw,state))
                current = self.state_getter()
                if current and self.locator.hand_signature(current) == signature:
                    self.idle_hand_signature = signature
        except asyncio.CancelledError:
            raise
        except Exception:
            # Optional read-only preparation must never select an action or
            # stop a fresh decision. A later fresh frame uses full recognition.
            return

    def _recognize_candidates(self, raw, state, actions):
        boxes = {}
        if any(a.kind in ('discard','riichi') for a in actions):
            try:
                boxes.update({('tile',(i,)):box for i,box in self.locator.hand(raw,state).items()})
            except InputDeferred:
                pass
        for kind in sorted({a.kind for a in actions} - {'discard'}):
            try:
                boxes[(kind,())] = self.locator.button(raw,kind)
            except InputDeferred:
                pass
        self._save_uncalibrated_offers(raw,state,actions)
        return boxes

    def _save_uncalibrated_offers(self,raw,state,actions):
        """Retain an existing frame, with no extra capture or input choice."""
        try:
            window = state.operation.window_id
            if self.offered_window != window:
                self.offered_window,self.offered_saved = window,set()
            templates = self.locator.profile()['templates']
            age = time.monotonic()-state.operation.received_mono
            stage = sum(age >= threshold for threshold in (.35, .9, 1.6)) - 1
            missing = {a.kind for a in actions if a.kind != 'discard'
                       and 'button_'+a.kind not in templates and (a.kind,stage) not in self.offered_saved}
            if not missing or stage < 0:
                return
            folder = self.browser.settings.runtime / 'calibration' / 'offered'
            folder.mkdir(parents=True,exist_ok=True)
            name = hashlib.sha256((state.match_id+window).encode()).hexdigest()[:24]+f'-{stage}'
            (folder/(name+'.png')).write_bytes(raw)
            (folder/(name+'.json')).write_text(json.dumps({
                'match_id':state.match_id,'round_uid':state.round_uid,
                'window_id':window,'state_version':state.state_version,
                'missing_buttons':sorted(missing),'offered_kinds':sorted({a.kind for a in actions}),
                'observation_stage':stage,'window_age_ms':round(age*1000),
                'source':'parallel_observation_existing_frame','extra_captures':0,
            },ensure_ascii=False,indent=2),encoding='utf8')
            self.offered_saved.update((kind,stage) for kind in missing)
        except (OSError,ValueError,InputDeferred):
            # Optional diagnostic evidence cannot affect the selected action.
            return

    async def observe(self, state, actions, deadline):
        """Observe every offered target while the model thinks; never press."""
        if self.input_mode != 'browser_background' or not self.window:
            return
        self.observed_targets = {}
        try:
            async with self.lease.hold('observe:'+state.operation.window_id):
                async def neutral():
                    await self.browser.prepare_background()
                    await self.browser.page.mouse.move(5,5)
                    await asyncio.sleep(.05)
                await finish_input(neutral())
                while time.monotonic() < deadline:
                    current = self.state_getter()
                    if not current or current.match_id != state.match_id or current.state_version != state.state_version:
                        return
                    try:
                        # Drain only the frame already started when the model
                        # becomes ready. Publishing belongs to that transaction,
                        # so cancellation cannot discard a completed recognition
                        # and force execution to capture the same scene again.
                        await finish_input(self._observe_frame(state, actions, deadline))
                    except InputDeferred:
                        pass  # Optional observation; execution still has full recognition.
                    await asyncio.sleep(.15)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.metrics.append({'stage':'preobserve_unavailable','error_type':type(error).__name__})

    async def _observe_frame(self, state, actions, deadline):
        async with asyncio.timeout_at(deadline):
            self._observation_current(state, deadline)
            layout = await self.browser.layout_metrics()
            raw = await self.browser.image_bytes()
            observed = time.monotonic()
            boxes = await finish_input(asyncio.to_thread(self._recognize_candidates, raw, state, actions))
            if await self.browser.layout_metrics() != layout:
                return
            self._observation_current(state, deadline)
            canvas = layout.get('canvas') or {'x':0, 'y':0}
            cached = {}
            for key, box in boxes.items():
                point = (box.center[0]+canvas['x'], box.center[1]+canvas['y'])
                cached[key] = Target(point, (0,0,0,0), observed,
                    'parallel_visible_observation', point, layout, raw, box.confidence)
            self.observed_targets = {(state.match_id, state.state_version, state.operation.window_id):cached}

    async def bind(self, hwnd):
        matches = [w for w in self.mouse.windows() if w.hwnd == hwnd]
        if len(matches) != 1 or matches[0].pid != await self.browser.process_id():
            raise ValueError("game window not found")
        self.window = matches[0]
        self.mouse.stop.clear()

    async def bind_dedicated(self):
        pid = await self.browser.process_id()
        windows = [w for w in self.mouse.windows() if w.pid == pid]
        if len(windows) != 1:
            raise InputDeferred('dedicated browser window is not unique')
        await self.bind(windows[0].hwnd)

    def _observation_current(self,state,deadline=None):
        current=self.state_getter()
        if (not current or not current.operation or current.match_id != state.match_id
                or current.state_version != state.state_version
                or current.operation.window_id != state.operation.window_id):
            raise InputDeferred('observation superseded before press')
        if self.mouse.stop.is_set():
            raise InputDeferred('input stopped during observation')
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('input deadline expired during observation')

    async def target(self, semantic, state, indices=None, deadline=None):
        self._observation_current(state,deadline)
        key = (state.match_id,state.state_version,state.operation.window_id)
        cached = getattr(self,'observed_targets',{}).get(key,{}).get((semantic,tuple(indices or [])))
        if cached and time.monotonic()-cached.observed <= .6:
            self.metrics.append({'semantic':semantic,'stage':'preobserved_target','age_ms':(time.monotonic()-cached.observed)*1000})
            return cached
        # Recognition can occasionally be delayed by the compositor/CPU. A
        # stale result may be discarded and observed again before any press.
        for _ in range(3):
            target = await self._target_once(semantic, state, indices, deadline)
            self._observation_current(state,deadline)
            if time.monotonic()-target.observed <= 1.2:
                return target
        raise TargetExpired('recognized layout repeatedly expired before input')

    async def _target_once(self, semantic, state, indices=None, deadline=None):
        stage_started = time.monotonic()
        def mark(stage):
            nonlocal stage_started
            now = time.monotonic()
            self.metrics.append({'semantic':semantic,'stage':stage,'elapsed_ms':(now-stage_started)*1000})
            stage_started = now
        if not self.window:
            raise InputDeferred("no window bound")
        if self.input_mode=='browser_background':
            # Clear a stale hovered/lifted tile without touching the OS cursor.
            await self.browser.prepare_background()
            await self.browser.page.mouse.move(5,5)
            await asyncio.sleep(.05)
        self._observation_current(state,deadline)
        mark('prepare_pointer')
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
        layout = await self.browser.layout_metrics()
        # Unity draws its controls inside the canvas; a Laya stage traversal
        # cannot expose those pixels. Other renderers keep semantic-node support.
        nodes = [] if layout.get('canvas') else await self.browser.visible_nodes()
        self._observation_current(state,deadline)
        mark('layout_and_visible_nodes')
        observed = time.monotonic()
        raw, confidence = None, None
        aliases = BUTTONS.get(semantic, ())
        found = [n for n in nodes if n["text"].strip() in aliases or n["name"] in aliases]
        if semantic == "tile":
            # Exact, visible tile nodes may be supplied by a validated UI adapter.
            found = [n for n in nodes if n.get("name") == f"hand_tile_{indices[0]}" and n.get("text") == state.hand[indices[0]]]
        if len(found) == 1:
            node = found[0]
            evidence = "visible_ui_semantics"
        else:
            for attempt in range(3):
                self._observation_current(state,deadline)
                raw = await self.browser.image_bytes()
                self._observation_current(state,deadline)
                mark('capture_and_crop')
                observed = time.monotonic()
                try:
                    if semantic == "riichi_tile":
                        box = await asyncio.to_thread(self.locator.riichi_tile,raw,state,indices[0])
                    elif semantic == "tile":
                        box = await asyncio.to_thread(self.locator.hand,raw,state)
                        box = box[indices[0]]
                    elif semantic.startswith("combo:"):
                        kind = semantic.split(":",1)[1]
                        tiles = [state.hand[i] for i in indices]
                        from .rules import legal_actions
                        kinds = {'ankan','kakan','daiminkan'} if kind in ('ankan','kakan','daiminkan') else {kind}
                        offered = {tuple(sorted(state.hand[i] for i in a.consume_indices))
                                   for a in legal_actions(state) if a.kind in kinds}
                        box = await asyncio.to_thread(self.locator.combo,raw,kind,tiles,offered)
                    else:
                        box = await asyncio.to_thread(self.locator.button,raw,semantic)
                    mark('recognition')
                    self._observation_current(state,deadline)
                    break
                except InputDeferred:
                    folder = self.locator.folder / 'failures'
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / (hashlib.sha256(raw).hexdigest()[:20]+'.png')).write_bytes(raw)
                    current = self.state_getter()
                    if attempt == 2 or not current or current.state_version != state.state_version:
                        raise
                    # Reobserve the same intended target after an animation;
                    # never press another button or weaken glyph validation.
                    await asyncio.sleep(.06)
            node = {"x":box.center[0],"y":box.center[1]}
            evidence = "calibrated_screenshot_matching"
            confidence = box.confidence
        if await self.browser.layout_metrics() != layout:
            raise InputDeferred('layout changed during recognition')
        self._observation_current(state,deadline)
        if self.input_mode == 'browser_background':
            geometry = {'x':0,'y':0,'ratio':1,'canvas':layout.get('canvas')}
        else:
            geometry = await self.browser.page.evaluate("""() => ({x:window.screenX+(window.outerWidth-window.innerWidth)/2,
              y:window.screenY+window.outerHeight-window.innerHeight,ratio:window.devicePixelRatio,
              canvas:document.querySelector('#unity-canvas')?.getBoundingClientRect().toJSON()})""")
        canvas=geometry.get("canvas") if evidence=="calibrated_screenshot_matching" else None
        x = round((geometry["x"] + node["x"] + (canvas["x"] if canvas else 0)) * geometry["ratio"])
        y = round((geometry["y"] + node["y"] + (canvas["y"] if canvas else 0)) * geometry["ratio"])
        page_point=(node['x']+(canvas['x'] if canvas else 0),node['y']+(canvas['y'] if canvas else 0))
        mark('geometry_recheck')
        return Target((x, y), rect, observed, evidence,page_point,layout,raw,confidence)

    def _current(self, state, action):
        current = self.state_getter()
        if not current or not current.operation or current.operation.window_id != action.window_id:
            raise InputDeferred("operation no longer current")
        if current.state_version != state.state_version:
            raise InputDeferred("state changed before input")

    async def execute(self, state: Snapshot, action: LegalAction, deadline):
        async with self.lease.hold("gameplay:"+action.window_id):
            self.metrics = []
            self.target_evidence = []
            try:
                return await self._execute(state, action, deadline)
            finally:
                # Persist the frames already used to identify non-discard
                # targets. This adds no capture to the action's critical path.
                if self.target_evidence:
                    await finish_input(asyncio.to_thread(self._save_target_evidence))

    def _save_target_evidence(self):
        folder = self.browser.settings.runtime / 'input-evidence'
        try:
            folder.mkdir(parents=True,exist_ok=True)
            for raw,step in self.target_evidence:
                filename = hashlib.sha256(raw).hexdigest()[:24]+'.png'
                (folder/filename).write_bytes(raw)
                step['screenshot'] = filename
        except OSError as error:
            # Diagnostics cannot turn a sent click into an unsent action or
            # cause another press. Keep the geometry and mark missing evidence.
            self.metrics.append({'stage':'target_evidence_unavailable','error_type':type(error).__name__})
        finally:
            self.target_evidence = []

    async def _execute(self, state: Snapshot, action: LegalAction, deadline):
        self._current(state, action)

        async def click(semantic, indices=None):
            for attempt in range(3):
                started = time.monotonic()
                try:
                    return await click_once(semantic,indices)
                except TargetExpired:
                    self.metrics.append({'semantic':semantic,'attempt':attempt+1,'stage':'expired_before_press',
                                         'elapsed_ms':(time.monotonic()-started)*1000})
                    if attempt == 2 or time.monotonic() >= deadline:
                        raise

        async def click_once(semantic,indices=None):
            target_start = time.monotonic()
            target = await self.target(semantic, state, indices, deadline)
            self.metrics.append({'semantic':semantic,'stage':'target','elapsed_ms':(time.monotonic()-target_start)*1000})
            if action.kind != 'discard':
                step = {'semantic':semantic,'stage':'target_evidence',
                        'window_id':action.window_id,'state_version':state.state_version,
                        'page_point':target.page_point,'screen_point':target.point,
                        'observed_mono':target.observed,'source':target.evidence,
                        'confidence':target.confidence}
                self.metrics.append(step)
                if target.frame:
                    self.target_evidence.append((target.frame,step))
            self._current(state, action)
            if self.input_mode=='browser_background':
                if self.mouse.stop.is_set() or self.mouse.user.GetAsyncKeyState(0x1B)&0x8000:
                    self.mouse.stop.set()
                    raise InputDeferred('input stopped')
                if time.monotonic()-target.observed>1.2:
                    raise TargetExpired('background layout expired')
                async def validate():
                    self._current(state, action)
                    if await self.browser.layout_metrics() != target.layout:
                        raise InputDeferred('background geometry changed before press')
                    if time.monotonic()-target.observed > 1.2:
                        raise TargetExpired('background target expired before press')
                press_started = time.monotonic()
                try:
                    return await self.browser.background_click(target.page_point,deadline,self.mouse.stop,
                                                               validate=validate)
                finally:
                    self.metrics.append({'semantic':semantic,'stage':'dispatch_and_frames',
                                         'elapsed_ms':(time.monotonic()-press_started)*1000})
            return await finish_input(asyncio.to_thread(
                self.mouse.click, self.window, target.rect, target.point, target.observed, deadline))

        if action.kind == "discard":
            return await click("tile", [action.tile_index])
        if action.kind == "riichi":
            # Verify the whole hand before dimming makes full segmentation
            # unavailable. Following clicks target only the offered bright tile.
            await self.target('tile', state, [action.tile_index], deadline)
            await click("riichi")
            # UI selection is independent of the authoritative state version.
            for _ in range(15):
                if time.monotonic() >= deadline:
                    raise TimeoutError("riichi selection expired")
                try:
                    return await click("riichi_tile", [action.tile_index])
                except InputDeferred:
                    await asyncio.sleep(0.02)
            raise InputDeferred("riichi selection not verified")
        if action.kind in ("chi", "pon", "daiminkan", "ankan", "kakan"):
            from .rules import legal_actions
            button_kinds = {"daiminkan", "ankan", "kakan"} if action.kind in ("daiminkan", "ankan", "kakan") else {action.kind}
            choices = {(a.kind, tuple(sorted(state.hand[i] for i in a.consume_indices)), a.meld_id)
                       for a in legal_actions(state) if a.kind in button_kinds}
            if len(choices) == 1:
                # Unity immediately submits a sole meld choice. There is no
                # combination menu, even if two identical hand instances are
                # represented by several equivalent legal action IDs.
                return await click(action.kind)
            # A combination crop is required before opening the menu, so an
            # unsupported menu never strands the UI after the first click.
            profile = self.locator.profile()
            tiles = [state.hand[i] for i in action.consume_indices]
            label = "combo_"+action.kind+"_"+"_".join(sorted(tiles))
            if label not in profile["templates"] and 'combo_region' not in profile:
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
