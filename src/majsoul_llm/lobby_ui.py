"""Visible canvas navigation using local, screen-specific calibration.

No game internals are inspected. Calibration images belong to the local client
and stay under ignored runtime/navigation, separate from gameplay templates.
"""
from __future__ import annotations

import asyncio
import json
import time

import cv2
import numpy as np

from .input_lease import finish_input
from .game_modes import FOUR_PLAYER_LENGTHS
from .lobby import LobbyError, WaitingUser
from .locator import decode_image, glyph_similarity


class LobbyUI:
    def __init__(self, browser, executor):
        self.browser, self.executor = browser, executor
        self.folder = browser.settings.runtime / "navigation"

    def profile(self):
        path = self.folder / "profile.json"
        if not path.exists():
            raise WaitingUser("lobby_calibration_required")
        profile = json.loads(path.read_text(encoding="utf-8"))
        if profile.get("version") != "lobby.screen.v1":
            raise LobbyError("unsupported_lobby_calibration")
        return profile

    def _image(self, raw, profile):
        image = decode_image(raw)
        height, width = image.shape[:2]
        if abs(width/height/(profile["width"]/profile["height"])-1) > .01:
            raise WaitingUser("lobby_layout_changed_recalibrate")
        return cv2.resize(image, (profile["width"], profile["height"]))

    def _matches(self, image, marker):
        x, y, w, h = marker["box"]
        patch_path = (self.folder / marker["file"]).resolve()
        if not patch_path.is_relative_to(self.folder.resolve()):
            raise LobbyError("invalid_lobby_calibration_path")
        patch = decode_image(patch_path.read_bytes())
        region = image[max(0,y-3):y+h+3, max(0,x-3):x+w+3]
        if region.shape[0] < patch.shape[0] or region.shape[1] < patch.shape[1] or float(np.std(patch)) < 3:
            return False
        return glyph_similarity(region, patch) >= marker.get("threshold", .94)

    def _scene(self, image, profile):
        found = [name for name, scene in profile["scenes"].items()
                 if scene.get("anchors") and all(self._matches(image, a) for a in scene["anchors"])]
        if found:
            priority = max(profile["scenes"][name].get("priority", 0) for name in found)
            found = [name for name in found if profile["scenes"][name].get("priority", 0) == priority]
        if len(found) != 1:
            raise WaitingUser("lobby_screen_not_uniquely_recognized")
        return found[0]

    async def observe(self):
        profile = self.profile()
        image = self._image(await self.browser.image_bytes(), profile)
        return self._scene(image, profile)

    async def wait_scene(self, expected=None, ctx=None, seconds=15):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            if ctx:
                ctx.check()
            try:
                scene = await self.observe()
                if expected is None or scene == expected:
                    return scene
            except WaitingUser:
                pass
            await asyncio.sleep(.1)
        raise WaitingUser("lobby_screen_not_ready")

    async def _click(self, scene, control, ctx, *, submit=None):
        ctx.check()
        await self.wait_scene(scene, ctx)
        profile = self.profile()
        raw = await self.browser.image_bytes()
        image = self._image(raw, profile)
        if self._scene(image, profile) != scene:
            raise WaitingUser("lobby_screen_changed")
        target = profile["scenes"][scene]["controls"].get(control)
        if target is None:
            raise WaitingUser("lobby_control_not_calibrated")
        if scene == "room" and control in ("ready", "start") and not target.get("anchor"):
            raise WaitingUser("room_ready_label_calibration_required")
        searched = None
        if target.get("search"):
            for _ in range(18):
                searched = self._find(image, target["search"], target["search_region"])
                if searched:
                    break
                ctx.check()
                if self.executor.mouse.stop.is_set() or self.executor.mouse.user.GetAsyncKeyState(0x1B) & 0x8000:
                    raise LobbyError("navigation_stopped")
                layout = await self.browser.layout_metrics()
                canvas = layout["canvas"]
                x, y, w, h = target["search_region"]
                original = await self.browser.page.evaluate('() => window.__mjLocalPointer')
                await self.browser.page.mouse.move(canvas["x"]+(x+w/2)*canvas["width"]/profile["width"],
                                                   canvas["y"]+(y+h/2)*canvas["height"]/profile["height"])
                ctx.check()
                await self.browser.page.mouse.wheel(0, 600*target.get("scroll_direction", 1))
                if original:
                    await self.browser.page.mouse.move(original["x"], original["y"])
                await asyncio.sleep(.15)
                image = self._image(await self.browser.image_bytes(), profile)
                if self._scene(image, profile) != scene:
                    raise WaitingUser("lobby_screen_changed")
            if searched is None:
                raise WaitingUser("requested_tier_not_visible")
        if target.get("anchor") and not self._matches(image, target["anchor"]):
            raise WaitingUser("lobby_control_not_visible_or_available")
        layout = await self.browser.layout_metrics()
        self.browser.require_visible_canvas(layout)
        canvas = layout["canvas"]
        x, y = searched or target["point"]
        if not (0 < x < profile["width"] and 0 < y < profile["height"]):
            raise LobbyError("invalid_lobby_control_point")
        point = (canvas["x"]+x*canvas["width"]/profile["width"],
                 canvas["y"]+y*canvas["height"]/profile["height"])
        observed = time.monotonic()
        # No navigation/resize can turn a previously observed target into a
        # press in another view, including while waiting for a render frame.
        page = self.browser.page
        async def validate_geometry():
            ctx.check()
            actual = await self.browser.layout_metrics()
            if self.browser.page is not page or actual != layout:
                raise LobbyError("lobby_geometry_changed")
            ctx.check()
        await validate_geometry()
        ctx.check()
        if self.executor.mouse.user.GetAsyncKeyState(0x1B) & 0x8000:
            self.executor.mouse.stop.set()
            raise LobbyError("navigation_stopped")
        if submit:
            ctx.before_submit(submit)
        if self.executor.input_mode == "browser_background":
            await finish_input(self.browser.background_click(point, observed+1.2, self.executor.mouse.stop,
                                                              validate=validate_geometry))
        else:
            window = self.executor.window
            if not window:
                raise WaitingUser("bind_dedicated_window_first")
            geometry = await page.evaluate("""() => ({x:screenX+(outerWidth-innerWidth)/2,
              y:screenY+outerHeight-innerHeight,dpr:devicePixelRatio})""")
            screen_point = tuple(round((geometry[k]+p)*geometry["dpr"]) for k, p in zip(("x", "y"), point, strict=True))
            rect = self.executor.mouse.rect(window)
            await finish_input(asyncio.to_thread(self.executor.mouse.click, window, rect, screen_point, observed, observed+1.2))
        await asyncio.sleep(.15)

    def _find(self, image, marker, region):
        path = (self.folder / marker["file"]).resolve()
        if not path.is_relative_to(self.folder.resolve()):
            raise LobbyError("invalid_lobby_calibration_path")
        patch = decode_image(path.read_bytes())
        x, y, w, h = region
        ph, pw = patch.shape[:2]
        if ph > h or pw > w or float(np.std(patch)) < 3:
            return None
        scores = cv2.matchTemplate(image[y:y+h, x:x+w], patch, cv2.TM_CCOEFF_NORMED)
        _, score, _, point = cv2.minMaxLoc(scores)
        threshold = marker.get("threshold", .94)
        if score < threshold:
            return None
        px, py = point
        scores[max(0,py-ph//2):py+ph//2+1, max(0,px-pw//2):px+pw//2+1] = -1
        if cv2.minMaxLoc(scores)[1] >= threshold:
            raise WaitingUser("lobby_control_ambiguous")
        return x+px+pw/2, y+py+ph/2

    async def click(self, scene, control, ctx, *, submit=None):
        return await self._click(scene, control, ctx, submit=submit)

    async def selected(self, scene, control):
        if self.executor.input_mode == "browser_background":
            # Button hover enlarges its text. Read the stable, unhovered option.
            await self.browser.page.mouse.move(5, 5)
            await asyncio.sleep(.08)
        profile = self.profile()
        image = self._image(await self.browser.image_bytes(), profile)
        if self._scene(image, profile) != scene:
            return False
        marker = profile["scenes"][scene]["controls"].get(control, {}).get("selected")
        return bool(marker and self._matches(image, marker))

    async def select(self, scene, control, ctx):
        await self.wait_scene(scene, ctx)
        if not await self.selected(scene, control):
            await self.click(scene, control, ctx)
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            ctx.check()
            if await self.selected(scene, control):
                return
            # Selection/hover color transitions can outlive the mouse release.
            # Observe the same click settling; never click it a second time.
            await asyncio.sleep(.1)
        raise WaitingUser("lobby_selection_not_verified")

    async def enter_room_code(self, code, ctx):
        # This Unity client uses an on-canvas number pad, not an HTML input.
        await self.click("join_form", "clear", ctx)
        if await self.read_room_code() != "":
            raise LobbyError("room_code_clear_not_verified")
        entered = ""
        for digit in code:
            await self.click("join_form", "digit_"+digit, ctx)
            entered += digit
            if await self.read_room_code() != entered:
                raise LobbyError("room_code_readback_mismatch")

    def _read_digits(self, image, scene):
        x, y, w, h = scene["digit_region"]
        mask = (cv2.cvtColor(image[y:y+h, x:x+w], cv2.COLOR_BGR2GRAY) > 180).astype("uint8")*255
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        components = sorted((s for s in stats[1:count] if s[4] >= 10), key=lambda s:s[0])
        result = ""
        for cx, cy, cw, ch, _ in components:
            patch = mask[cy:cy+ch, cx:cx+cw]
            scores = []
            for digit, file in scene["digits"].items():
                path = (self.folder / file).resolve()
                if not path.is_relative_to(self.folder.resolve()):
                    raise LobbyError("invalid_lobby_calibration_path")
                template = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
                if abs(cw/ch/(template.shape[1]/template.shape[0])-1) > .25:
                    continue
                normalized = cv2.resize(patch, (template.shape[1], template.shape[0]))
                score = glyph_similarity(normalized, template)
                # Centering the whole number moves glyph edges across the
                # canvas pixel grid. Preserve shape under a one-pixel bounds
                # change as well as comparing the normalized crop.
                if abs(cw-template.shape[1]) <= 2 and abs(ch-template.shape[0]) <= 2:
                    padded = cv2.copyMakeBorder(patch, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=0)
                    score = max(score, glyph_similarity(padded, template))
                scores.append((score, digit))
            scores.sort(reverse=True)
            if not scores or scores[0][0] < .9 or len(scores)>1 and scores[0][0]-scores[1][0] < .05:
                raise WaitingUser("room_code_digit_ambiguous")
            result += scores[0][1]
        return result

    async def read_room_code(self):
        profile = self.profile()
        image = self._image(await self.browser.image_bytes(), profile)
        if self._scene(image, profile) != "join_form":
            raise WaitingUser("room_code_dialog_changed")
        return self._read_digits(image, profile["scenes"]["join_form"])


class LobbyDriver:
    capabilities = {"login", "ranked", "join_friend", "create_friend"}

    def __init__(self, browser, session, ui):
        self.browser, self.session, self.ui = browser, session, ui

    async def _wait(self, predicate, ctx, seconds=12):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            ctx.check()
            result = predicate()
            if result:
                return result
            await asyncio.sleep(.1)
        raise WaitingUser("waiting_for_visible_login_or_server_result")

    async def _login(self, ctx):
        await self.browser.start()
        if self.ui.executor.input_mode == "browser_background":
            await self.browser.prepare_background()
        ctx.stage("browser_ready")
        ctx.stage("authenticating")
        # Credentials are never accepted by this command or passed to the LLM.
        # Persistent profile may authenticate automatically. New credentials and
        # verification currently require the visible client; tracked in DEV-037.
        await self._wait(lambda: self.session.authenticated, ctx, seconds=20)
        if self.session.has_game:
            raise WaitingUser("existing_game_requires_reconnect")
        ctx.stage("lobby")

    async def _home(self, ctx):
        for _ in range(4):
            scene = await self.ui.wait_scene(ctx=ctx, seconds=30)
            if scene == "lobby":
                return
            if scene not in ("friend_menu", "create_form", "join_form", "ranked_tiers", "ranked_modes"):
                raise WaitingUser("return_to_lobby_manually")
            await self.ui.click(scene, "back", ctx)
        raise WaitingUser("return_to_lobby_manually")

    async def run(self, command, ctx):
        await self._login(ctx)
        if command.kind == "login":
            ctx.stage("completed")
            return {"outcome":"session_authenticated", "has_room":bool(self.session.room)}
        if self.session.room:
            raise WaitingUser("already_in_room_no_navigation")
        await self._home(ctx)
        ctx.stage("navigating")
        if command.kind == "ranked":
            await self.ui.click("lobby", "ranked", ctx)
            await self.ui.click("ranked_tiers", command.tier, ctx)
            # The final four-player option starts matching immediately.
            before = time.monotonic()
            await self.ui.click("ranked_modes", command.length, ctx, submit="matchGame")
            await self._ack("matchGame", before, ctx)
            ctx.stage("matching")
            return {"outcome":"matching", "tier":command.tier, "length":command.length,
                    "in_game":False}
        await self.ui.click("lobby", "friend", ctx)
        if command.kind == "join_friend":
            await self.ui.click("friend_menu", "join", ctx)
            await self.ui.enter_room_code(command.room_code, ctx)
            before = time.monotonic()
            await self.ui.click("join_form", "submit", ctx, submit="joinRoom")
            ack = await self._ack("joinRoom", before, ctx)
            room = ack.get("room")
            # Server room IDs are integers. Compare numerically while keeping
            # the original string in the command and input/readback records.
            if not room or int(room["room_code"]) != int(command.room_code):
                raise LobbyError("joined_room_not_verified")
            ctx.stage("room")
            if command.ready:
                return await self._prepare(command, room, ctx)
        else:
            pair = (command.thinking.fixed_seconds, command.thinking.bank_seconds)
            if pair not in {(3, 5), (5, 10), (5, 20), (60, 0), (300, 0)}:
                raise LobbyError("thinking_time_not_supported_by_current_client")
            await self.ui.click("friend_menu", "create", ctx)
            for control in ("four", command.length, f"time_{pair[0]}_{pair[1]}", "advanced_off"):
                await self.ui.select("create_form", control, ctx)
            # Read every selected field again immediately before creating.
            for control in ("four", command.length, f"time_{pair[0]}_{pair[1]}", "advanced_off"):
                if not await self.ui.selected("create_form", control):
                    raise LobbyError("create_options_changed")
            before = time.monotonic()
            await self.ui.click("create_form", "submit", ctx, submit="createRoom")
            ack = await self._ack("createRoom", before, ctx)
            room = ack.get("room")
            self._verify_created(command, room)
        ctx.stage("room")
        return {"outcome":"room", "room":room, "ready_or_started":False}

    async def _ack(self, method, before, ctx):
        ack = await self._wait(lambda: (r if (r := self.session.latest.get(method)) and r["observed_mono"] >= before else None), ctx)
        ctx.confirmed(method, ack)
        if ack["error_code"]:
            raise LobbyError("server_rejected_"+str(ack["error_code"]), definitive=True)
        return ack

    @staticmethod
    def _verify_created(command, room):
        # The AI practice option has one human seat but still uses the visible
        # four-player board. max_player_count is human capacity, not board size.
        if not room or room.get("players") != (1 if command.length == "ai" else 4):
            raise LobbyError("created_room_players_not_verified")
        code = room.get("room_code")
        if not isinstance(code, str) or not code.isascii() or not code.isdigit() or int(code) == 0:
            raise LobbyError("created_room_code_not_verified")
        mode = room.get("mode", {})
        # GameMode.ai is also true in ordinary four-player friend rooms with
        # zero robots. It does not encode the length selected on the form.
        if mode.get("mode") != FOUR_PLAYER_LENGTHS[command.length]:
            raise LobbyError("created_room_length_not_verified")
        detail = mode.get("detail_rule", {})
        if (detail.get("time_fixed"), detail.get("time_add")) != (command.thinking.fixed_seconds, command.thinking.bank_seconds):
            raise LobbyError("created_room_time_not_verified")

    async def _prepare(self, command, room, ctx):
        ctx.bind_room(self.session, room["room_code"])
        if self.session.prepared is not True:
            await self.ui.wait_scene("room", ctx)
            before = time.monotonic()
            # Requires a calibrated visible ready label. The room owner's
            # start button must never be accepted at the same screen position.
            await self.ui.click("room", "ready", ctx, submit="readyPlay")
            ack = await self._ack("readyPlay", before, ctx)
            if ack.get("ready") is not True:
                raise LobbyError("room_ready_state_not_verified")
        return {"outcome":"room", "room":room, "prepared":True, "started_by_bot":False,
                "current_room_verified":True}

    async def reconcile(self, command, ctx):
        record = ctx.navigator.journal.get(command.command_id)
        if not record["submitted_step"]:
            # Waiting for manual login before any submission is safe to retry.
            ctx.navigator.journal.update(command.command_id, stage="stopped")
            return await self.run(command, ctx)
        method = record["submitted_step"]
        ack = record.get("confirmation")
        if ack is None:
            # Only an original response arriving late can confirm this command.
            # No new create/join/match or guessed room equivalence is allowed.
            ack = await self._ack(method, time.monotonic(), ctx)
        elif ack["method"] != method:
            raise LobbyError("confirmation_method_mismatch")
        if ack["error_code"]:
            raise LobbyError("server_rejected_"+str(ack["error_code"]), definitive=True)
        if command.kind == "create_friend":
            self._verify_created(command, ack.get("room"))
        elif command.kind == "join_friend":
            joined = record.get("confirmations", {}).get("joinRoom", ack if method == "joinRoom" else {})
            room = joined.get("room")
            if not room or int(room["room_code"]) != int(command.room_code):
                raise LobbyError("joined_room_not_verified")
            if method == "readyPlay":
                if ack.get("ready") is not True:
                    raise LobbyError("room_ready_state_not_verified")
                ctx.stage("completed")
                return {"outcome":"room_submission_confirmed", "room":room, "prepared":True,
                        "confirmation_scope":"historical_submission", "current_room_verified":False,
                        "started_by_bot":False}
            # Pre-upgrade records had no ready field and must retain their
            # original behavior when explicitly resumed after a code upgrade.
            if record["command"].get("ready", False):
                ctx.stage("room")
                return await self._prepare(command, room, ctx)
        # The persisted response proves this submission's historical outcome.
        # It cannot prove that the client is still in that room after reconnect.
        if command.kind in ("create_friend", "join_friend"):
            ctx.stage("completed")
            return {"outcome":"room_submission_confirmed", "room":ack.get("room"),
                    "confirmation_scope":"historical_submission", "current_room_verified":False,
                    "ready_or_started":False}
        raise WaitingUser("matching_current_state_requires_fresh_verification")
