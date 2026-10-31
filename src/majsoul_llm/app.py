"""Loopback control panel with separate read and control credentials."""
from __future__ import annotations

import hmac
import hashlib
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

from .browser import Browser
from .config import ROOT, Settings
from .coordinator import Coordinator
from .client_guard import ClientGuard
from .engine import Engine
from .execution import Executor
from .input_lease import InputLease
from .lobby import LobbyError, LobbyJournal, Navigator
from .lobby_commands import LobbyCommand
from .lobby_ui import LobbyDriver, LobbyUI
from .model import ModelClient
from .native import InputDeferred
from .protocol import Protocol
from .practice import PracticeRooms, RoomAction
from .queries import Queries
from .review import Reviewer
from .round_flow import RoundFlow
from .store import Store
from .usage import usage_summary


class Service:
    def __init__(self, settings):
        self.settings = settings
        sources = sorted((ROOT/'src'/'majsoul_llm').glob('*.py'))
        self.runtime_build = {'source_digest':hashlib.sha256(b''.join(p.read_bytes() for p in sources)).hexdigest(),
                              'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()}
        self.store = Store(settings.runtime / "archive.sqlite")
        self.store.mark_interrupted()
        schema = settings.runtime / "protocol/protocol.proto"
        protocol = Protocol.from_proto(schema, settings.runtime / "generated") if schema.exists() else None
        self.engine = Engine(self.store, protocol, recovery_timer_verified=settings.recovery_timer_verified)
        self.browser = Browser(settings, self.engine.frame)
        self.client = ModelClient(settings, self.store)
        self.coordinator = Coordinator(settings, self.engine, self.client)
        self.input_lease = InputLease()
        self.executor = Executor(self.browser, lambda:self.engine.state, self.input_lease)
        self.coordinator.executor = self.executor
        self.lobby_journal = LobbyJournal(settings.runtime / "lobby.sqlite")
        self.lobby_ui = LobbyUI(self.browser, self.executor)
        self.navigator = Navigator(self.lobby_journal, LobbyDriver(self.browser, self.engine.lobby_session, self.lobby_ui),
                                   self.input_lease, self.executor.mouse.stop,
                                   lambda:bool(self.engine.state and self.engine.state.lifecycle == "active"),
                                   lambda:self.coordinator.enabled)
        self.reviewer = Reviewer(self.store, self.client,
                                 lambda:self.coordinator.enabled or self.coordinator.active() or bool(self.coordinator.annotation_tasks))
        self.engine.on_state = self.changed
        self.engine.on_input = self.coordinator.input_observed
        self.queries = Queries(self.store, settings.read_token.get_secret_value(), self.engine.capture_health)
        self.practice = PracticeRooms(self)
        self.round_flow = RoundFlow(self)
        self.client_guard = ClientGuard(self)

    async def changed(self, state, event):
        was_enabled = self.coordinator.enabled
        if state and state.operation:
            await self.executor.cancel_idle_observation()
            await self.reviewer.pause_for_decision()
        await self.coordinator.changed(state, event)
        await self.executor.refresh_idle_hand(state,was_enabled and not self.coordinator.shadow)
        await self.round_flow.changed(state, was_enabled and not self.coordinator.shadow)
        await self.client_guard.changed(state, was_enabled and not self.coordinator.shadow)
        if state and state.lifecycle == 'ended':
            self.coordinator.enabled = False
            self.coordinator.status = 'match_complete'
        if state and state.lifecycle in ("ended", "interrupted"):
            await self.reviewer.enqueue(state.match_id)

    async def close(self):
        await self.client_guard.stop()
        await self.round_flow.stop()
        await self.navigator.stop()
        await self.coordinator.stop("service_stopped")
        await self.engine.interrupt()
        await self.browser.close()
        if self.reviewer.worker:
            self.reviewer.worker.cancel()
            try:
                await self.reviewer.worker
            except __import__("asyncio").CancelledError:
                pass
        await self.client.close()
        self.store.close()
        self.lobby_journal.close()

    def status(self):
        state = self.engine.state
        return {"connected":bool(self.browser.context), "protocol_loaded":bool(self.engine.protocol),
                "protocol_hash":self.engine.protocol.fingerprint if self.engine.protocol else None,
                "enabled":self.coordinator.enabled, "shadow":self.coordinator.shadow, "status":self.coordinator.status,
                "bound_window":self.executor.window.__dict__ if self.executor.window else None,
                "input_mode":self.executor.input_mode,
                'vision_cache':{'hand_hits':self.executor.locator.hand_cache_hits,
                                'hand_misses':self.executor.locator.hand_cache_misses,
                                'idle_active':bool(self.executor.idle_observation and not self.executor.idle_observation.done())},
                "runtime_build":self.runtime_build,
                "result_flow":{"active":bool(self.round_flow.task and not self.round_flow.task.done()),
                               "last_scene":self.round_flow.last_scene},
                "state":state.model_dump(mode="json") if state else None, "plan":self.coordinator.plan,
                "time_bank":self.coordinator.bank.remaining, "timer_verified":self.settings.timer_verified,
                'recovery_timer_verified':self.settings.recovery_timer_verified,
                "model":self.settings.model, "compact_enabled":self.settings.compact_validated,
                'model_config':self.settings.model_status(),
                'table_wire_enabled':self.settings.compact_validated and self.settings.table_wire_validated,
                'stream_actions':self.settings.stream_actions,
                'stream_action_format':self.settings.stream_action_format,
                'stream_hedge_delay_seconds':self.settings.stream_hedge_delay_seconds,
                "navigation":self.navigator.status(), "login_session":self.engine.lobby_session.public(),
                "diagnostics":self.browser.errors[-5:], "input_error":self.engine.last_error,
                "capture_health":self.engine.capture_health(),
                "token_usage":self.store.records("token_usage", limit=20),
                "decision_annotations":self.store.records('decision_annotation',limit=10),
                "usage_summary":usage_summary(self.store, self.client.credential_id),
                "receipts":self.store.records("receipt", limit=10)}


def create_app(settings=None, service=None):
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app):
        app.state.service = service or Service(settings)
        if not service:
            await app.state.service.reviewer.resume_pending()
        yield
        await app.state.service.close()

    app = FastAPI(title="小肥鱼麻将", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    if service:
        app.state.service = service

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        host = request.url.hostname
        if host not in ("127.0.0.1", "localhost", "::1", "testserver"):
            return HTMLResponse("Invalid host", status_code=403)
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
                return HTMLResponse("Invalid origin", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
        return response

    def auth(request:Request, *, control=False):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        accepted = [settings.control_token.get_secret_value()]
        if not control:
            accepted.append(settings.read_token.get_secret_value())
        if not any(hmac.compare_digest(token, key) for key in accepted):
            raise HTTPException(401, "invalid credential")

    def reader(request:Request):
        auth(request)
        return request.app.state.service.queries

    def controller(request:Request):
        auth(request, control=True)
        return request.app.state.service

    def navigator(request:Request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not any(hmac.compare_digest(token, key.get_secret_value())
                   for key in (settings.navigation_token, settings.control_token)):
            raise HTTPException(401, "invalid credential")
        return request.app.state.service.navigator

    @app.exception_handler(LobbyError)
    async def lobby_error(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error":error.code}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error":"record_not_found"}, status_code=404)

    @app.exception_handler(InputDeferred)
    async def unavailable_input(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({'error':'input_unavailable','reason':str(error)},status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error":"invalid_request_or_unavailable_state"}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    async def panel():
        return (Path(__file__).parent / "panel.html").read_text(encoding="utf-8")

    base = "/api/v1/mahjong"

    @app.get(base+"/live")
    async def live(q=Depends(reader)):
        return q.live()

    @app.get(base+"/matches")
    async def matches(scope_cursor:str|None=None, cursor:str|None=None, status:str|None=None,
                      completeness:str|None=None, limit:int=Query(5,ge=1,le=20), q=Depends(reader)):
        return q.matches(scope_cursor=scope_cursor,cursor=cursor,status=status,completeness=completeness,limit=limit)

    @app.get(base+"/matches/{id}")
    async def match(id:str,q=Depends(reader)):
        return q.match(id)

    @app.get(base+"/rounds/{id}")
    async def round(id:str,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),q=Depends(reader)):
        return q.round(id,offset,limit)

    @app.get(base+"/big-hands")
    async def big_hands(scope_cursor:str|None=None,offset:int=Query(0,ge=0),limit:int=Query(10,ge=1,le=20),q=Depends(reader)):
        return q.big_hands(scope_cursor,offset,limit)

    @app.get(base+"/memories")
    async def memories(query:str="",status:str|None=None,offset:int=Query(0,ge=0),limit:int=Query(8,ge=1,le=50),q=Depends(reader)):
        return q.memories(query,status,offset=offset,limit=limit)

    @app.get(base+"/memories/{id}")
    async def memory(id:str,offset:int=Query(0,ge=0),limit:int=Query(8,ge=1,le=50),q=Depends(reader)):
        return q.memories(id=id,offset=offset,limit=limit)

    @app.get("/control/status")
    async def status(s=Depends(controller)):
        return s.status()

    navigation_base = "/api/v1/mahjong/navigation"

    @app.get(navigation_base)
    async def navigation_status(n=Depends(navigator)):
        return n.status()

    @app.post(navigation_base+"/commands", status_code=202)
    async def navigate(command:LobbyCommand, n=Depends(navigator)):
        return await n.submit(command.model_dump(mode="json"))

    @app.get(navigation_base+"/commands/{command_id}")
    async def navigation_command(command_id:str, n=Depends(navigator)):
        return n.journal.get(command_id)

    @app.post(navigation_base+"/commands/{command_id}/resume", status_code=202)
    async def navigation_resume(command_id:str, n=Depends(navigator)):
        return await n.resume(command_id)

    @app.post(navigation_base+"/commands/{command_id}/cancel")
    async def navigation_cancel(command_id:str, n=Depends(navigator)):
        return await n.cancel(command_id)

    @app.get("/control/windows")
    async def windows(s=Depends(controller)):
        return [w.__dict__ for w in s.executor.mouse.windows()]

    @app.post("/control/room")
    async def room_action(command:RoomAction, s=Depends(controller)):
        return await s.practice.run(command)

    @app.get("/control/screenshot")
    async def screenshot(s=Depends(controller)):
        return Response(await s.browser.image_bytes(),media_type="image/png")

    @app.post("/control/calibration")
    async def calibration(request:Request,s=Depends(controller)):
        data = await request.json()
        # Crop only the current bound browser screenshot; never accept arbitrary files.
        return s.executor.locator.save_crop(await s.browser.image_bytes(), data["label"], data["box"],
                                            hand_region=data.get("hand_region"))

    @app.post("/control/{action}")
    async def control(action:str,request:Request,s=Depends(controller)):
        data = await request.json()
        if action == "browser":
            if s.navigator.busy():
                raise LobbyError("navigation_in_progress")
            await s.browser.start()
        elif action == 'reattach-browser':
            if s.navigator.busy() or s.coordinator.enabled or s.coordinator.active() or s.input_lease.owner:
                raise LobbyError('input_owner_active')
            await s.browser.attach_existing(int(data['pid']))
        elif action == "fit-window":
            await s.navigator.stop()
            await s.coordinator.stop('window_layout_changed')
            await s.browser.fit_window()
        elif action == "bind":
            if s.navigator.busy():
                raise LobbyError("navigation_in_progress")
            await s.executor.bind(int(data["hwnd"]))
        elif action == "shadow":
            if s.navigator.busy():
                raise LobbyError("navigation_in_progress")
            await s.coordinator.start(shadow=True)
        elif action == "start":
            if s.navigator.busy():
                raise LobbyError("navigation_in_progress")
            if not s.executor.window:
                raise ValueError("bind a game window first")
            await s.coordinator.start(shadow=False)
        elif action == 'confirm-results':
            await s.round_flow.resume()
        elif action=='input-mode':
            mode=data.get('mode')
            if mode not in ('physical_foreground','browser_background'):
                raise ValueError('unsupported input mode')
            await s.navigator.stop()
            await s.coordinator.stop('input_mode_changed')
            if mode == 'physical_foreground':
                await s.browser.release_background()
            s.executor.input_mode=mode
        elif action in ("pause", "stop", "emergency"):
            await s.navigator.stop()
            await s.coordinator.stop(action)
        else:
            raise HTTPException(404,"unknown control")
        return {"ok":True,"status":s.coordinator.status}

    return app
