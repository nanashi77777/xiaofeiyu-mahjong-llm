import asyncio
import json
import threading
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import httpx
from pydantic import ValidationError

from majsoul_llm.app import create_app
from majsoul_llm.config import Settings
from majsoul_llm.engine import room_rules
from majsoul_llm.input_lease import InputBusy, InputLease, finish_input
from majsoul_llm.lobby import LobbyError, LobbyJournal, Navigator, WaitingUser
from majsoul_llm.lobby_commands import parse_command
from majsoul_llm.lobby_session import LobbySession, sanitize_lobby
from majsoul_llm.lobby_ui import LobbyDriver, LobbyUI


def test_commands_explicit_parameters_and_zeroes():
    command = parse_command({"kind":"join_friend", "command_id":"test-1", "room_code":"001234"})
    assert command.room_code == "001234"
    assert command.ready is True
    assert not parse_command({"kind":"join_friend", "command_id":"observe", "room_code":"001234", "ready":False}).ready
    ranked = parse_command({"kind":"ranked", "command_id":"test-2", "tier":"银之间", "length":"四人南"})
    assert (ranked.tier, ranked.length) == ("silver", "south")
    invalid = [
        {"kind":"join_friend", "room_code":1234},
        {"kind":"join_friend", "room_code":"１２３４"},
        {"kind":"join_friend", "room_code":"12345", "ready":"false"},
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
    room = {"room_code":"12345", "players":4,"mode":{"mode":2,"ai":True,"detail_rule":{"time_fixed":5,"time_add":10}}}
    for ai in (True, False):
        LobbyDriver._verify_created(command, {**room, "mode":{**room["mode"], "ai":ai}})
    for patch in ({"players":3}, {"room_code":None}, {"mode":{}}, {"mode":{"mode":1}},
                  {"mode":{"mode":2,"detail_rule":{"time_fixed":5000,"time_add":10000}}}):
        with pytest.raises(LobbyError):
            LobbyDriver._verify_created(command, {**room, **patch})


@pytest.mark.parametrize(("length", "mode"), [("single",4), ("east",1), ("south",2), ("ai",3)])
def test_verified_four_player_modes_are_shared_with_game_rule_entry(length, mode):
    command = parse_command({"command_id":"mode", "kind":"create_friend", "players":4, "length":length,
                             "thinking":{"fixed_seconds":3,"bank_seconds":5}})
    room = {"room_code":"12345", "players":1 if length == "ai" else 4, "mode":{"mode":mode,"ai":True,
            "detail_rule":{"time_fixed":3,"time_add":5}}}
    LobbyDriver._verify_created(command, room)
    assert "unverified_four_player_mode" not in room_rules({"category":1,"mode":room["mode"]}).unsupported
    for unverified in (None,0,11,12,999):
        assert "unverified_four_player_mode" in room_rules({"mode":{"mode":unverified}}).unsupported


def test_room_projection_excludes_free_text_and_server_errors_do_not_replace_room():
    private = {"room_id":12345, "max_player_count":4, "owner_id":123,
               "persons":[{"nickname":"private-name"}], "mode":{"mode":2, "ai":True,
               "extendinfo":"private-extension", "testing_environment":{"paixing":123},
               "detail_rule":{"time_fixed":5, "time_add":10, "unknown":"private-rule"}}}
    data = sanitize_lobby("createRoom", {"room":private})
    assert data["room"]["mode"] == {"mode":2,"ai":True,"detail_rule":{"time_fixed":5,"time_add":10}}
    assert "private" not in json.dumps(data)
    session = LobbySession()
    session.observe({"method":".lq.Lobby.createRoom", "data":data, "connection":"c"})
    original = session.room
    session.observe({"method":".lq.Lobby.joinRoom", "data":{**data,"error_code":123}, "connection":"c"})
    assert session.room is original
    session.observe({"method":".lq.Lobby.fetchRoom", "data":{"error_code":0}, "connection":"c"})
    assert session.room is None


async def test_confirmation_survives_validation_error_and_restart_without_clicks(tmp_path):
    command = {"command_id":"persisted", "kind":"create_friend", "players":4, "length":"south",
               "thinking":{"fixed_seconds":5, "bank_seconds":10}}
    room = {"room_code":"12345", "players":4, "playing":False, "robots":0,
            "mode":{"mode":2,"ai":True,"extendinfo":"private-extension",
                    "detail_rule":{"time_fixed":5,"time_add":10}}}
    session = LobbySession()
    session.latest["createRoom"] = {"error_code":0, "room":room, "observed_mono":float("inf")}
    driver = LobbyDriver(None, session, None)
    async def run(command, ctx):
        ctx.stage("browser_ready")
        ctx.stage("lobby")
        ctx.stage("navigating")
        ctx.before_submit("createRoom")
        await driver._ack("createRoom", 0, ctx)
        raise LobbyError("previous_adapter_validation_bug")
    driver.run = run
    n, _ = navigation(tmp_path, driver)
    await n.submit(command)
    await n.task
    record = n.journal.get("persisted")
    assert record["status"] == "uncertain" and record["confirmation"]["room"]["room_code"] == "12345"
    assert "private" not in json.dumps(record) and "observed_mono" not in json.dumps(record)
    n.journal.close()
    # No browser or UI exists after restart, so any input would fail the test.
    n, _ = navigation(tmp_path, LobbyDriver(None, LobbySession(), None))
    await n.resume("persisted")
    await n.task
    result = n.journal.get("persisted")
    assert result["status"] == "succeeded" and result["stage"] == "completed"
    assert result["result"]["confirmation_scope"] == "historical_submission"
    assert result["result"]["current_room_verified"] is False
    assert result["result"]["ready_or_started"] is False
    n.journal.close()


async def test_failed_confirmation_is_durable_but_never_confirms_success(tmp_path):
    class Rejected(Driver):
        async def run(self, command, ctx):
            ctx.before_submit("createRoom")
            driver = LobbyDriver(None, SimpleNamespace(latest={"createRoom":{
                "observed_mono":float("inf"), "error_code":123}}), None)
            await driver._ack("createRoom", 0, ctx)
    n, _ = navigation(tmp_path, Rejected())
    await n.submit({"command_id":"rejected", "kind":"create_friend", "players":4, "length":"south",
                    "thinking":{"fixed_seconds":5,"bank_seconds":10}})
    await n.task
    result = n.journal.get("rejected")
    assert result["status"] == "failed" and result["reason"] == "server_rejected_123"
    assert result["confirmation"]["error_code"] == 123
    n.journal.close()


@pytest.mark.parametrize('fixture_error',[101,102,103])
async def test_join_rejection_never_prepares_or_rejoins_on_duplicate(tmp_path,fixture_error):
    # Synthetic errors stand for a server refusal (missing/full/password etc.);
    # they are not asserted to be the production client's numeric mapping.
    calls = []
    class RejectedJoin(Driver):
        async def run(self, command, ctx):
            calls.append(command.room_code)
            ctx.before_submit('joinRoom')
            driver = LobbyDriver(None,SimpleNamespace(latest={'joinRoom':{
                'observed_mono':float('inf'),'error_code':fixture_error}}),None)
            await driver._ack('joinRoom',0,ctx)
            raise AssertionError('rejected room must never prepare')
    n,_ = navigation(tmp_path,RejectedJoin())
    command = {'command_id':'join-rejected','kind':'join_friend','room_code':'00123','ready':True}
    await n.submit(command)
    await n.task
    result = n.journal.get(command['command_id'])
    assert result['status'] == 'failed' and result['reason'] == 'server_rejected_'+str(fixture_error)
    assert result['result'] is None and result['confirmation']['method'] == 'joinRoom'
    assert not result.get('confirmations',{}).get('readyPlay')
    assert (await n.submit(command))['status'] == 'failed' and calls == ['00123']
    n.journal.close()


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


async def test_joined_room_resume_only_prepares_and_persists_both_steps(tmp_path):
    session = LobbySession()
    session.authenticated = True
    session.room = {"room_code":"12345", "players":4, "playing":False, "robots":0,
                    "mode":{"mode":2,"detail_rule":{"time_fixed":5,"time_add":10}}}
    calls = []
    class UI:
        calibrated = False
        async def wait_scene(self, scene, ctx):
            ctx.check()
            if not self.calibrated:
                raise WaitingUser("ready_calibration_required")
        async def click(self, scene, control, ctx, *, submit):
            ctx.before_submit(submit)
            calls.append(control)
            session.latest["readyPlay"] = {"observed_mono":float("inf"), "error_code":0,"ready":True}
    ui = UI()
    driver = LobbyDriver(None, session, ui)
    async def run(command, ctx):
        ctx.stage("browser_ready")
        ctx.stage("lobby")
        ctx.stage("navigating")
        ctx.before_submit("joinRoom")
        session.latest["joinRoom"] = {"observed_mono":float("inf"),"error_code":0,"room":session.room}
        await driver._ack("joinRoom", 0, ctx)
        ctx.stage("room")
        return await driver._prepare(command, session.room, ctx)
    driver.run = run
    n, _ = navigation(tmp_path, driver)
    command = {"command_id":"join-ready", "kind":"join_friend", "room_code":"12345"}
    await n.submit(command)
    await n.task
    assert n.journal.get("join-ready")["status"] == "waiting_user" and not calls
    ui.calibrated = True
    await n.resume("join-ready")
    await n.task
    record = n.journal.get("join-ready")
    assert record["status"] == "succeeded" and record["result"]["prepared"]
    assert set(record["confirmations"]) == {"joinRoom", "readyPlay"} and calls == ["ready"]
    assert record["confirmation"]["ready"] is True
    # A later identical request does not toggle ready off or rejoin.
    assert (await n.submit(command))["status"] == "succeeded" and calls == ["ready"]
    n.journal.close()


async def test_prepare_guard_rechecks_room_after_wait_and_never_clicks_another_room(tmp_path):
    session = LobbySession()
    session.authenticated = True
    session.room = {"room_code":"12345", "playing":False}
    clicks = []
    class UI:
        async def wait_scene(self, scene, ctx):
            session.room = {"room_code":"99999", "playing":False}
        async def click(self, scene, control, ctx, *, submit):
            ctx.before_submit(submit)
            clicks.append(control)
    driver = LobbyDriver(None, session, UI())
    async def run(command, ctx):
        ctx.stage("browser_ready")
        return await driver._prepare(command, dict(session.room), ctx)
    driver.run = run
    n, _ = navigation(tmp_path, driver)
    await n.submit({"command_id":"room-changed", "kind":"join_friend", "room_code":"12345"})
    await n.task
    record = n.journal.get("room-changed")
    assert record["status"] == "waiting_user" and record["submitted_step"] is None and not clicks
    n.journal.close()


async def test_unknown_prepare_resume_only_accepts_late_original_response(tmp_path):
    n, _ = navigation(tmp_path)
    command = parse_command({"command_id":"ready-unknown", "kind":"join_friend", "room_code":"12345"})
    n.journal.create(command)
    room = {"room_code":"12345", "players":4}
    n.journal.update(command.command_id, status="uncertain", stage="room", submitted_step="readyPlay",
                     confirmation=None, confirmations={"joinRoom":{"method":"joinRoom","error_code":0,"room":room}})
    # No browser/UI is present. Repeated resume cannot click an uncertain ready.
    session = LobbySession()
    driver = LobbyDriver(None, session, None)
    async def no_response(predicate, ctx, seconds=12):
        raise WaitingUser("waiting_for_original_ready_confirmation")
    driver._wait = no_response
    n.driver = driver
    await n.resume(command.command_id)
    await n.task
    assert n.journal.get(command.command_id)["status"] == "waiting_user"
    # Restore the normal observer wait after an original delayed response.
    del driver._wait
    session.latest["readyPlay"] = {"observed_mono":float("inf"), "error_code":0, "ready":True}
    await n.resume(command.command_id)
    await n.task
    result = n.journal.get(command.command_id)
    assert result["status"] == "succeeded" and result["result"]["prepared"]
    assert not result["result"]["current_room_verified"]
    n.journal.close()


def test_canvas_room_code_keeps_leading_zeroes_and_rejects_ambiguous_glyphs(tmp_path):
    ui = object.__new__(LobbyUI)
    ui.folder = tmp_path
    digits = {}
    for digit in "0123456789":
        glyph = np.zeros((45,32),dtype=np.uint8)
        cv2.putText(glyph,digit,(4,34),cv2.FONT_HERSHEY_SIMPLEX,1,(255,),2,cv2.LINE_AA)
        _,_,stats,_ = cv2.connectedComponentsWithStats((glyph > 180).astype("uint8")*255)
        x,y,w,h,_ = max(stats[1:],key=lambda s:s[4])
        glyph = (glyph[y:y+h,x:x+w] > 180).astype("uint8")*255
        file = digit+".png"
        cv2.imwrite(str(tmp_path/file),glyph)
        digits[digit] = file
    screen = np.zeros((60,240,3),dtype=np.uint8)
    for index,digit in enumerate("054321"):
        glyph = cv2.imread(str(tmp_path/digits[digit]),0)
        h,w = glyph.shape
        screen[10:10+h,10+index*35:10+index*35+w] = glyph[:,:,None]
    scene = {"digit_region":[0,0,240,60],"digits":digits}
    assert ui._read_digits(screen,scene) == "054321"
    scene["digits"]["2"] = scene["digits"]["1"]
    with pytest.raises(WaitingUser,match="room_code_digit_ambiguous"):
        ui._read_digits(screen,scene)
