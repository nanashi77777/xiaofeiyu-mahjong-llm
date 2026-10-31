"""Dedicated browser and passive CDP WebSocket listener."""
from __future__ import annotations

import asyncio
import base64
import inspect
from pathlib import Path

from playwright.async_api import async_playwright

from .config import Settings
from .native import InputDeferred


class Browser:
    def __init__(self, settings: Settings, on_message):
        self.settings, self.on_message = settings, on_message
        self.context = self.page = self.cdp = self.playwright = None
        self.queue = asyncio.Queue(maxsize=1000)
        self.consumer = None
        self.errors = []
        self.input_cdp = None
        self.capture_lock = asyncio.Lock()

    async def prepare_background(self):
        if self.input_cdp:
            return self.input_cdp
        session = await self.page.context.new_cdp_session(self.page)
        try:
            await session.send('Emulation.setFocusEmulationEnabled', {'enabled': True})
            # A minimized Chromium window may stop compositor frames even with
            # timer throttling disabled. A native-size acknowledged screencast
            # keeps canvas frames running without requesting scaled rendering;
            # frames are discarded and never archived.
            async def acknowledge(event):
                try:
                    await session.send('Page.screencastFrameAck', {'sessionId': event['sessionId']})
                except Exception:
                    pass  # Session can close while its final frame is queued.
            session.on('Page.screencastFrame', lambda e: asyncio.create_task(acknowledge(e)))
            await session.send('Page.startScreencast', {'format':'jpeg', 'quality':10,
                                                       'everyNthFrame':1})
        except BaseException:
            await session.detach()
            raise
        self.input_cdp = session
        return session

    async def release_background(self):
        session, self.input_cdp = self.input_cdp, None
        if session:
            try:
                await session.send('Page.stopScreencast')
                await session.send('Emulation.setFocusEmulationEnabled', {'enabled':False})
            finally:
                await session.detach()

    async def start(self):
        if self.context:
            return
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            str(self.settings.runtime / "browser-profile"), executable_path=self.settings.chrome_path,
            headless=False, no_viewport=True, args=["--start-maximized", "--disable-background-timer-throttling",
                                                "--disable-renderer-backgrounding","--disable-backgrounding-occluded-windows",
                                                "--disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling",
                                                f"--remote-debugging-port={self.settings.debug_port}","--remote-debugging-address=127.0.0.1"])
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self.cdp = await self.context.new_cdp_session(self.page)
        await self.fit_window()
        await self.cdp.send("Network.enable")
        await self.page.add_init_script("""(() => { window.__mjLocalPointer=null;
          addEventListener('pointermove',e=>{window.__mjLocalPointer={x:e.clientX,y:e.clientY}},true); })()""")
        self.cdp.on("Network.webSocketFrameReceived", lambda msg: self._enqueue(msg, False))
        self.cdp.on("Network.webSocketFrameSent", lambda msg: self._enqueue(msg, True))
        owner = getattr(self.on_message, "__self__", None)
        if owner and hasattr(owner, "browser_reset"):
            self.page.on("framenavigated", lambda frame: owner.browser_reset() if frame == self.page.main_frame else None)
            self.cdp.on("Network.webSocketClosed", lambda msg: owner.socket_closed(msg["requestId"]))
        self.consumer = asyncio.create_task(self._consume())
        await self.page.goto(self.settings.game_url, wait_until="domcontentloaded", timeout=60000)

    async def fit_window(self):
        """Maximize the page's own native window using its real client viewport."""
        if not self.page or not self.cdp:
            raise ValueError("browser not started")
        window = await self.cdp.send("Browser.getWindowForTarget")
        if window["bounds"]["windowState"] in ("minimized", "fullscreen"):
            await self.cdp.send("Browser.setWindowBounds", {"windowId":window["windowId"],
                                                          "bounds":{"windowState":"normal"}})
        await self.cdp.send("Browser.setWindowBounds", {"windowId":window["windowId"],
                                                      "bounds":{"windowState":"maximized"}})
        # Native resize and Unity's resize listener complete asynchronously.
        for _ in range(20):
            actual = await self.cdp.send("Browser.getWindowForTarget")
            if actual["bounds"]["windowState"] == "maximized":
                break
            await asyncio.sleep(.05)
        else:
            raise InputDeferred("dedicated browser could not maximize")
        return actual["bounds"]

    async def layout_metrics(self):
        if not self.page:
            raise ValueError("browser not started")
        return await self.page.evaluate("""() => {
          const canvas=document.querySelector('#unity-canvas');
          return {width:innerWidth,height:innerHeight,dpr:devicePixelRatio,
            canvas:canvas?canvas.getBoundingClientRect().toJSON():null};
        }""")

    async def process_id(self):
        if not self.page or not self.page.context.browser:
            raise InputDeferred('dedicated browser identity unavailable')
        session = await self.page.context.browser.new_browser_cdp_session()
        try:
            processes = (await session.send('SystemInfo.getProcessInfo'))['processInfo']
            return next(int(p['id']) for p in processes if p['type'] == 'browser')
        finally:
            await session.detach()

    @staticmethod
    def require_visible_canvas(layout):
        canvas = layout.get("canvas")
        if canvas is None:
            return
        if (canvas["width"] <= 0 or canvas["height"] <= 0 or canvas["x"] < -1 or canvas["y"] < -1
                or canvas["right"] > layout["width"] + 1 or canvas["bottom"] > layout["height"] + 1):
            raise InputDeferred("game canvas is clipped; restore the dedicated browser size")

    def _enqueue(self, message, from_client):
        response = message.get("response", {})
        if response.get("opcode") != 2:
            return
        try:
            self.queue.put_nowait((message["requestId"], base64.b64decode(response["payloadData"]), from_client))
        except asyncio.QueueFull:
            self.errors.append("websocket queue overflow; state must resync")
            owner=getattr(self.on_message,"__self__",None)
            if owner and hasattr(owner,"gap"):
                asyncio.create_task(owner.gap("websocket_queue_overflow"))

    async def _consume(self):
        while True:
            connection, raw, direction = await self.queue.get()
            try:
                await self.on_message(connection, raw, direction)
            except Exception as error:
                # Protocol bytes and exception text might contain account data.
                self.errors.append(type(error).__name__)
                self.errors[:] = self.errors[-20:]
            finally:
                self.queue.task_done()

    async def visible_nodes(self):
        if not self.page:
            return []
        # Only traverse visible UI, never DesktopMgr hidden hands or the wall.
        return await self.page.evaluate("""() => {
          if (!globalThis.Laya?.stage) return [];
          const out=[];
          function walk(n,path,depth){
            if(!n || n.visible===false || n.alpha===0 || depth>24) return;
            const name=String(n.name||''), text=String(n.text||n.label||'');
            if(n.width>0 && n.height>0 && (name||text)) {
              try { const p=n.localToGlobal(new Laya.Point(n.width/2,n.height/2));
                out.push({path:path+'/'+name,name,text:text.slice(0,80),x:p.x,y:p.y,w:n.width,h:n.height});
              }catch{}
            }
            for(const child of n._children||[])walk(child,path+'/'+name,depth+1);
          }
          walk(Laya.stage,'',0); return out.slice(0,3000);
        }""")

    async def screenshot(self, path: Path):
        if self.page:
            await self.page.screenshot(path=str(path))

    async def image_bytes(self):
        if not self.page:
            raise ValueError("browser not started")
        async with self.capture_lock:
            page = self.page
            before = await self.layout_metrics()
            self.require_visible_canvas(before)
            # No element screenshot, clip, CSS scale or device-metrics override.
            # Crop the native compositor image locally, keeping Unity's viewport
            # independent of how often recognition or the panel requests images.
            session = self.cdp
            temporary = session is None
            if temporary:
                session = await page.context.new_cdp_session(page)
            try:
                result = await session.send("Page.captureScreenshot", {
                    "format":"png", "fromSurface":True, "captureBeyondViewport":False})
            finally:
                if temporary:
                    await session.detach()
            after = await self.layout_metrics()
            if self.page is not page or after != before:
                raise InputDeferred("layout changed during capture")
            return self.crop_viewport(base64.b64decode(result["data"]), before)

    @staticmethod
    def crop_viewport(raw, layout):
        import cv2
        from .locator import decode_image
        image = decode_image(raw)
        ih, iw = image.shape[:2]
        vw, vh = layout["width"], layout["height"]
        if vw <= 0 or vh <= 0 or abs(iw/vw/(ih/vh)-1) > .01:
            raise InputDeferred("viewport image scale mismatch")
        canvas = layout.get("canvas")
        if canvas:
            x1, y1 = round(canvas["x"]*iw/vw), round(canvas["y"]*ih/vh)
            x2, y2 = round(canvas["right"]*iw/vw), round(canvas["bottom"]*ih/vh)
            if not (0 <= x1 < x2 <= iw and 0 <= y1 < y2 <= ih):
                raise InputDeferred("canvas crop outside viewport")
            image = image[y1:y2, x1:x2]
            width, height = round(canvas["width"]), round(canvas["height"])
        else:
            width, height = vw, vh
        return cv2.imencode(".png", cv2.resize(image, (width, height)))[1].tobytes()

    async def background_click(self,point,deadline,stop,validate=None):
        """Browser mouse events only: no game API calls or game packets."""
        import time
        from urllib.parse import urlsplit
        if not self.page or urlsplit(self.page.url).netloc != urlsplit(self.settings.game_url).netloc:
            raise ValueError('bound game page is unavailable')
        if stop.is_set() or time.monotonic()>=deadline:
            raise TimeoutError('background input stopped or expired')
        await self.prepare_background()
        original=await self.page.evaluate('() => window.__mjLocalPointer')
        await self.page.mouse.move(*point)
        # Unity updates the hovered 3D tile on its render frame. A frame here
        # avoids dispatching a click against the previous hover target.
        try:
            await asyncio.wait_for(self.page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>resolve(true)))'),.4)
        except TimeoutError:
            raise TimeoutError('background renderer did not acknowledge a frame') from None
        if stop.is_set() or time.monotonic()>=deadline:
            raise TimeoutError('background input stopped before press')
        if validate:
            validation = validate()
            if inspect.isawaitable(validation):
                await validation
        if stop.is_set() or time.monotonic()>=deadline:
            raise TimeoutError('background input stopped after validation')
        try:
            await self.page.mouse.down()
            # Unity samples held input in its render loop. A fixed 30ms press
            # can fit between two frames in a background/low-FPS lobby. Two
            # callbacks guarantee a Unity tick between press and release even
            # when our callback runs before Unity's callback in the first frame.
            await asyncio.wait_for(self.page.evaluate('''() => new Promise(resolve=>
              requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))'''), .4)
        finally:
            await self.page.mouse.up()
        # Keep the target under the pointer until the release is sampled too.
        await asyncio.wait_for(self.page.evaluate(
            '() => new Promise(resolve=>requestAnimationFrame(()=>resolve(true)))'), .4)
        clicked=time.monotonic()
        if original and not self.page.is_closed():
            current=await self.page.evaluate('() => window.__mjLocalPointer')
            if current and abs(current['x']-point[0])<2 and abs(current['y']-point[1])<2:
                await self.page.mouse.move(original['x'],original['y'])
        return clicked

    async def close(self):
        owner = getattr(self.on_message, "__self__", None)
        if owner and hasattr(owner, "browser_reset"):
            owner.browser_reset()
        if self.consumer:
            self.consumer.cancel()
        await self.release_background()
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()
        self.context = None
