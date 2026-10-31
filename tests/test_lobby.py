import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
import httpx
from pydantic import ValidationError

from majsoul_llm.app import create_app
from majsoul_llm.config import Settings
from majsoul_llm.input_lease import InputBusy, InputLease, finish_input
from majsoul_llm.lobby import LobbyError, LobbyJournal, Navigator, WaitingUser
from majsoul_llm.lobby_commands import parse_command
from majsoul_llm.lobby_session import LobbySession, sanitize_lobby
from majsoul_llm.lobby_ui import LobbyDriver, LobbyUI


def test_commands_explicit_parameters_and_zeroes():
    command = parse_command({"kind":"join_friend", "command_id":"test-1", "room_code":"001234"})
    assert command.room_code == "001234"
    ranked = parse_command({"kind":"ranked", "command_id":"test-2", "tier":"银之间", "length":"四人南"})
    assert (ranked.tier, ranked.length) == ("silver", "south")
    invalid = [
        {"kind":"join_friend", "room_code":1234},
        {"kind":"join_friend", "room_code":"１２３４"},
        {"kind":"ranked", "length":"east"},
        {"kind":"ranked", "tier":"auto", "length":"south"},
        {"kind":"login", "password":"never-accepted"},
        {"kind":"create_friend", "players":3, "length":"south", "thinking":{"fixed_seconds":5,"bank_seconds":20}},
        {"kind":"create_friend", "players":4, "length":"south", "thinking":{"fixed_seconds":5.1,"bank_seconds":20}},
        {"kind":"create_friend", "players":4, "length":"south", "thinking":{"fixed_seconds":True,"bank_seconds":20}},
    ]
    for data in invalid:
        with pytest.raises(ValidationError):
            parse_command({**data, "command_id":"bad"})


class Driver:
    capabilities = {"login", "ranked", "join_friend", "create_friend"}

    def __init__(self):
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.submit_input = False
        self.waiting = False

    async def run(self, command, ctx):
        self.calls.append(("run", command.command_id))
        ctx.stage("browser_ready")
        self.started.set()
        if self.waiting:
            raise WaitingUser("visible_verification_required")
        if self.submit_input:
            ctx.before_submit("createRoom")
        await self.release.wait()
        ctx.check()
        return {"outcome":"fixture_only"}

    async def reconcile(self, command, ctx):
        self.calls.append(("inspect_only", command.command_id))
        return {"outcome":"observed_actual_state"}


def navigation(tmp_path, driver=None):
    journal = LobbyJournal(tmp_path / "lobby.sqlite")
    flags = {"game":False,"enabled":False}
    navigator = Navigator(journal, driver or Driver(), InputLease(), threading.Event(),
                          lambda:flags["game"], lambda:flags["enabled"])
    return navigator, flags


async def test_one_command_id_never_repeats_even_after_restart(tmp_path):
    n, _ = navigation(tmp_path)
    data = {"kind":"login", "command_id":"once"}
    await n.submit(data)
    await n.driver.started.wait()
    assert (await n.submit(data))["status"] == "running"
    with pytest.raises(LobbyError, match="command_id_conflict"):
        await n.submit({"command_id":"once","kind":"join_friend","room_code":"00123"})
    with pytest.raises(LobbyError, match="input_busy"):
        await n.submit({"command_id":"other","kind":"login"})
    n.driver.release.set()
    await n.task
    assert (await n.submit(data))["status"] == "succeeded"
    n.journal.close()
    n, _ = navigation(tmp_path)
    assert (await n.submit(data))["status"] == "succeeded"
    assert not n.driver.calls
    n.journal.close()


async def test_cancelled_submission_remains_uncertain_and_resume_only_observes(tmp_path):
    n, _ = navigation(tmp_path)
    n.driver.submit_input = True
    await n.submit({"kind":"login", "command_id":"unknown"})
    await n.driver.started.wait()
    await n.stop()
    assert n.lease.owner is None
    assert n.journal.get("unknown")["status"] == "uncertain"
    await n.resume("unknown")
    await n.task
    assert n.driver.calls == [("run","unknown"), ("inspect_only","unknown")]
    assert n.journal.get("unknown")["submitted_step"] == "createRoom"
    n.journal.close()


async def test_stop_before_task_start_and_restart_are_not_silent_replays(tmp_path):
    n, _ = navigation(tmp_path)
    await n.submit({"kind":"login", "command_id":"never-started"})
    await n.stop()
    assert n.journal.get("never-started")["status"] == "cancelled"
    command = parse_command({"kind":"login", "command_id":"crashed"})
    n.journal.create(command)
    n.journal.update("crashed", status="running", submitted_step="joinRoom")
    n.journal.close()
    journal = LobbyJournal(tmp_path / "lobby.sqlite")
    assert journal.get("crashed")["status"] == "uncertain"
    assert journal.get("crashed")["submitted_step"] == "joinRoom"
    journal.close()


async def test_active_game_and_gameplay_prevent_navigation_without_records(tmp_path):
    n, flags = navigation(tmp_path)
    for flag in ("game", "enabled"):
        flags[flag] = True
        with pytest.raises(LobbyError):
            await n.submit({"kind":"login","command_id":"blocked"})
        flags[flag] = False
    assert n.journal.recent() == []
    await n.submit({"kind":"login", "command_id":"became-active"})
    await n.driver.started.wait()
    flags["game"] = True
    n.driver.release.set()
    await n.task
    assert n.journal.get("became-active")["status"] == "failed"
    assert n.lease.owner is None
    n.journal.close()


async def test_waiting_user_releases_input_and_resume_is_idempotent(tmp_path):
    n, _ = navigation(tmp_path)
    n.driver.waiting = True
    await n.submit({"kind":"login", "command_id":"verification"})
    await n.task
    assert n.journal.get("verification")["status"] == "waiting_user"
    assert n.lease.owner is None
    await n.resume("verification")
    await n.resume("verification")
    await n.task
    assert n.driver.calls == [("run", "verification"), ("inspect_only", "verification")]
    n.journal.close()


async def test_native_input_cancellation_keeps_lease_until_thread_finishes():
    lease, started, release = InputLease(), threading.Event(), threading.Event()
    def native():
        started.set()
        release.wait(2)
    async def execute():
        async with lease.hold("gameplay"):
            await finish_input(asyncio.to_thread(native))
    task = asyncio.create_task(execute())
    await asyncio.to_thread(started.wait, 1)
    task.cancel()
    await asyncio.sleep(.01)
    with pytest.raises(InputBusy):
        async with lease.hold("navigation"):
            pytest.fail("overlapping input")
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lease.owner is None


async def test_navigation_token_cannot_read_private_control_and_read_token_cannot_navigate(tmp_path):
    settings = Settings(runtime=tmp_path)
    n, _ = navigation(tmp_path)
    service = SimpleNamespace(navigator=n, status=lambda:{"secret_control":"hidden"})
    base = "/api/v1/mahjong/navigation"
    nav = {"Authorization":"Bearer "+settings.navigation_token.get_secret_value()}
    read = {"Authorization":"Bearer "+settings.read_token.get_secret_value()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings, service)),base_url="http://testserver") as client:
        assert (await client.get(base, headers=nav)).status_code == 200
        assert (await client.get(base, headers=read)).status_code == 401
        assert (await client.post(base+"/commands",json={"kind":"login","command_id":"test"},headers=read)).status_code == 401
        assert (await client.get("/control/status", headers=nav)).status_code == 401
        assert (await client.post("/control/start",json={},headers=nav)).status_code == 401
        assert (await client.post(base+"/commands",json={},headers={**nav,"Origin":"https://example.com"})).status_code == 403
        assert (await client.post(base+"/commands",json={"kind":"ranked","command_id":"bad"},headers=nav)).status_code == 422
    n.journal.close()


def test_lobby_session_never_retains_credentials_and_socket_close_invalidates():
    private = {"access_token":"private-login-token", "account":{"nickname":"private-name"}, "account_id":123,
               "error":{"code":0,"message":"private-error"}, "game_info":{"game_uuid":"private-game"}}
    data = sanitize_lobby("login", private)
    assert data == {"error_code":0, "has_game":True}
    session = LobbySession()
    session.observe({"method":".lq.Lobby.login", "data":data, "connection":"c"})
    assert session.authenticated and session.has_game
    session.observe({"method":".lq.Lobby.loginBeat", "data":{"error_code":0}, "connection":"c"})
    assert session.has_game
    assert "private" not in json.dumps(session.__dict__)
    session.closed("other")
    assert session.authenticated
    session.closed("c")
    assert not session.authenticated and session.latest == {}


def test_created_room_verified_against_real_server_fields():
    command = parse_command({"command_id":"create", "kind":"create_friend", "players":4, "length":"south",
                             "thinking":{"fixed_seconds":5,"bank_seconds":10}})
    room = {"players":4,"mode":{"mode":2,"ai":False,"detail_rule":{"time_fixed":5,"time_add":10}}}
    LobbyDriver._verify_created(command, room)
    for patch in ({"players":3}, {"mode":{"mode":1}}, {"mode":{"mode":2,"detail_rule":{"time_fixed":5000,"time_add":10000}}}):
        with pytest.raises(LobbyError):
            LobbyDriver._verify_created(command, {**room, **patch})


async def test_ui_selection_waits_for_same_click_to_settle_without_reclicking():
    clicks, checks = [], []
    readings = iter([False, False, True])
    ui = object.__new__(LobbyUI)
    async def wait_scene(*args):
        return "create_form"
    async def selected(*args):
        return next(readings)
    async def click(*args):
        clicks.append(args)
    ui.wait_scene, ui.selected, ui.click = wait_scene, selected, click
    ctx = SimpleNamespace(check=lambda:checks.append(True))
    await ui.select("create_form", "time_5_10", ctx)
    assert len(clicks) == 1 and len(checks) == 2
