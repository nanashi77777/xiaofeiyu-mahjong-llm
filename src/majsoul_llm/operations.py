"""Typed Mahjong operations. No arbitrary URLs, process ids, clicks or credentials."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, TypeAdapter

from .config import MODEL_GATES, Settings, atomic_private_json
from .domain import Record
from .lobby import LobbyError
from .memory import Memories
from .model import ModelClient
from .model_profiles import ModelProfiles, ProfileError
from .practice import PracticeRooms, RoomAction
from .review import MemoryProposal
from .store import packed, utcnow
from .usage import usage_summary
from .yaku import YakuReference
from .knowledge import Knowledge, KnowledgeProposal, learning_status


class SimpleCommand(Record):
    action: Literal['browser_open','browser_reconnect','bind','fit_window','start','shadow',
                    'pause','stop','emergency','confirm_results','service_stop']


class ArmCommand(Record):
    action: Literal['arm']
    seconds: StrictInt = Field(default=600, ge=10, le=3600)
    shadow: StrictBool = False


class InputCommand(Record):
    action: Literal['input_mode']
    mode: Literal['physical_foreground','browser_background']


class SettingsPatch(Record):
    base_seconds: StrictInt | StrictFloat | None = Field(default=None, gt=0, le=300)
    bank_seconds: StrictInt | StrictFloat | None = Field(default=None, ge=0, le=300)
    execution_margin: StrictInt | StrictFloat | None = Field(default=None, ge=.1, le=3)
    request_timeout: StrictInt | StrictFloat | None = Field(default=None, ge=1, le=120)
    stream_hedge_delay_seconds: StrictInt | StrictFloat | None = Field(default=None, ge=0, le=5)
    model_attempts: StrictInt | None = Field(default=None, ge=1, le=2)
    target_retries: StrictInt | None = Field(default=None, ge=0, le=3)
    failure_window_limit: StrictInt | None = Field(default=None, ge=1, le=10)
    confirmation_wait_seconds: StrictInt | StrictFloat | None = Field(default=None, gt=0, le=5)
    connection_wait_attempts: StrictInt | None = Field(default=None, ge=1, le=3)
    connection_wait_seconds: StrictInt | StrictFloat | None = Field(default=None, gt=0, le=10)
    stream_actions: StrictBool | None = None
    stream_action_format: Literal['nested','short'] | None = None
    compact_validated: StrictBool | None = None
    table_wire_validated: StrictBool | None = None
    json_schema: StrictBool | None = None
    review_enabled: StrictBool | None = None
    memory_recall_enabled: StrictBool | None = None
    memory_recall_limit: StrictInt | None = Field(default=None, ge=0, le=8)
    idle_hand_enabled: StrictBool | None = None
    parallel_observation_enabled: StrictBool | None = None
    result_confirmation_enabled: StrictBool | None = None
    client_guard_enabled: StrictBool | None = None


class ConfigureCommand(Record):
    action: Literal['settings']
    patch: SettingsPatch


class RoomCommand(Record):
    action: Literal['room']
    room_code: StrictStr = Field(pattern=r'^[0-9]{1,12}$')
    operation: Literal['leave','confirm_leave','add_robot_0','add_robot_1','add_robot_2','add_robot_3',
                       'start','ready','unready']
    takeover: StrictBool = False


class ProfileCommand(Record):
    action: Literal['profile_select']
    profile_id: StrictStr = Field(pattern=r'^profile_[a-f0-9]{16}$')


class CaptureCommand(Record):
    action: Literal['profile_capture']


class ReviewCommand(Record):
    action: Literal['review_enqueue','review_cancel']
    match_id: StrictStr = Field(min_length=1, max_length=150)


class MemoryCommand(Record):
    action: Literal['memory_revise']
    match_id: StrictStr = Field(min_length=1, max_length=150)
    proposal: MemoryProposal


class TeachingCommand(Record):
    action: Literal['knowledge_write']
    source_quote: StrictStr = Field(min_length=2,max_length=600)
    proposal: KnowledgeProposal


class DisableMemoryCommand(Record):
    action: Literal['memory_disable']
    id: StrictStr = Field(min_length=1,max_length=150)
    revision: StrictInt = Field(ge=1)
    reason: StrictStr = Field(min_length=1,max_length=160)
    source_quote: StrictStr = Field(min_length=2,max_length=600)


class CalibrationCommand(Record):
    action: Literal['calibrate']
    label: StrictStr = Field(pattern=r'^(?:tile_[0-9][mpsz]|button_[a-z0-9_]{1,64}|combo_[a-z0-9_]{1,80})$')
    box: tuple[StrictInt,StrictInt,StrictInt,StrictInt]
    hand_region: tuple[StrictInt,StrictInt,StrictInt,StrictInt] | None = None


Command = Annotated[SimpleCommand | ArmCommand | InputCommand | ConfigureCommand | RoomCommand |
                    ProfileCommand | CaptureCommand | ReviewCommand | MemoryCommand | TeachingCommand |
                    DisableMemoryCommand | CalibrationCommand,
                    Field(discriminator='action')]
COMMAND = TypeAdapter(Command)

STARTUP_RECOVERY = {
    'ai_room_fills_opponents_on_start': {'action':'room','operation':'start','takeover':True,
                                       'note':'人机房由游戏安排电脑；开始前先预备接管，再点房主开始。'},
    'browser_not_connected': {'action':'browser_open','note':'先打开或重连专用浏览器，再确认登录。'},
    'authenticated_browser_required_before_arm': {'action':'browser_open','note':'等待专用浏览器完成登录后再arm。'},
    'active_game_required_use_arm': {'action':'arm','note':'当前没有活动对局；arm只等待一次入局，不会自动开局。'},
    'complete_game_state_required': {'action':'arm','note':'首局或恢复数据尚未完整；arm等待真实完整场况。'},
    'authenticated_game_required': {'action':'browser_reconnect','note':'需要重新观察当前对局的真实认证和恢复事件。'},
    'fresh_action_window_required': {'action':'arm','note':'等待下一次真实且可用的行动窗口，不重置计时。'},
    'action_deadline_expired': {'action':'arm','note':'当前行动期限已过；等待新事件，不重发旧操作。'},
}
WAITABLE_STARTUP = {'complete_game_state_required','fresh_action_window_required','action_deadline_expired'}


class OperationRequest(Record):
    command_id: StrictStr = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    command: Command


def profile_id(name):
    return 'profile_' + hashlib.sha256(name.encode()).hexdigest()[:16]


class Operations:
    def __init__(self, service):
        self.s = service
        self.task = None
        self.arm_task = None
        self.arm_id = None
        self.epoch = 0
        self.s.store.db.execute('CREATE TABLE IF NOT EXISTS operation_commands(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        rows = self.s.store.db.execute('SELECT id,payload FROM operation_commands').fetchall()
        for row in rows:
            record = json.loads(row[1])
            if record['status'] in ('queued','running','waiting_game'):
                self.update(row[0],status='interrupted',reason='service_restarted_authorization_revoked')
        self.permissions = self._format_permissions()

    def _format_permissions(self):
        s = self.s.settings
        path = s.runtime / 'format-certificates.json'
        certificates = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        identity = s.model_identity()
        if identity not in certificates:
            certificates[identity] = {gate:bool(getattr(s,gate)) for gate in MODEL_GATES}
            atomic_private_json(path,certificates)
        return certificates[identity]

    def get(self, cid):
        row = self.s.store.db.execute('SELECT payload FROM operation_commands WHERE id=?',(cid,)).fetchone()
        if not row:
            raise KeyError(cid)
        return json.loads(row[0])

    def update(self, cid, **changes):
        record = {**self.get(cid), **changes, 'updated_at':utcnow()}
        self.s.store.db.execute('UPDATE operation_commands SET payload=? WHERE id=?',(packed(record),cid))
        self.s.store.db.commit()
        return record

    def busy(self):
        return bool(self.task and not self.task.done())

    def idle(self, *, no_match=False):
        s = self.s
        if (s.coordinator.enabled or s.coordinator.active() or s.coordinator.pending or s.input_lease.owner
                or s.navigator.busy() or s.round_flow.task and not s.round_flow.task.done()):
            raise LobbyError('stop_gameplay_and_navigation_before_operation')
        if no_match and s.engine.state and s.engine.state.lifecycle == 'active':
            raise LobbyError('active_match_must_finish_before_operation')

    def profiles(self):
        s = self.s.settings
        book = ModelProfiles(s).load()
        return {'current_profile_id':profile_id(s.model_profile),'profiles':[
            {'profile_id':profile_id(name),'label':'本机配置 '+str(index+1),
             'current':name == s.model_profile,
             'validated_formats':{gate:bool(profile.settings(s,name).__getattribute__(gate)) for gate in MODEL_GATES}}
            for index,(name,profile) in enumerate(book.profiles.items())]}

    def settings(self):
        s = self.s.settings
        return {'editable':{key:getattr(s,key) for key in SettingsPatch.model_fields},
                'input_mode':self.s.executor.input_mode,
                'format_permissions':self.permissions,
                'verification_readonly':{'timer_verified':s.timer_verified,
                                         'recovery_timer_verified':s.recovery_timer_verified,
                                         'integration_test':s.integration_test},
                'restart_restores_autoplay':False}

    def status(self):
        s = self.s
        state = s.engine.state
        return {'connected':s.browser.connected,'enabled':s.coordinator.enabled,
                'autoplay_prepared':bool(self.arm_id and self.arm_task and not self.arm_task.done()),
                'shadow':s.coordinator.shadow,'status':s.coordinator.status,
                'input_mode':s.executor.input_mode,'window_bound':bool(s.executor.window),
                'input_busy':bool(s.input_lease.owner),'operation_busy':self.busy(),
                'waiting_game':self.get(self.arm_id) if self.arm_id else None,
                'lifecycle':state.lifecycle if state else None,
                'match_id':state.match_id if state else None,
                'consistent':state.consistent if state else None,
                'completeness':state.round_completeness if state else None,
                'login':s.engine.lobby_session.public(),
                'result_flow':{'active':bool(s.round_flow.task and not s.round_flow.task.done()),
                               'last_scene':s.round_flow.last_scene},
                'recovery':s.coordinator.recovery.public(),
                'review_enabled':s.settings.review_enabled,
                'review_busy':bool(s.reviewer.review_task and not s.reviewer.review_task.done()),
                'queued_reviews':len(s.reviewer.queued)}

    async def prepare_room_start(self, command):
        """Bind and persist a one-game grant before the room start press."""
        self.idle(no_match=True)
        s=self.s
        epoch=self.epoch
        session=s.engine.lobby_session
        generation=session.generation
        room=session.room
        if (not s.browser.connected or not session.authenticated or not room
                or room['room_code']!=command.room_code or room.get('playing')):
            raise LobbyError('authenticated_idle_room_required_before_start')
        if not s.settings.api_key.get_secret_value():
            raise LobbyError('model_configuration_required_before_start')
        existing=self.arm_id
        if existing:
            record=self.get(existing)
            if (not self.arm_task or self.arm_task.done() or record['status']!='waiting_game'
                    or record['request']['command'].get('shadow',False)
                    or record['request']['command']['action']=='shadow'):
                raise LobbyError('real_autoplay_authorization_required_before_start')
            bound_room=(record.get('result') or {}).get('room_code')
            if bound_room and bound_room!=command.room_code:
                raise LobbyError('room_changed_before_start')
        else:
            s.executor.mouse.stop.clear()
        if self.input_stopped():
            raise LobbyError('input_stopped_before_room_start')
        await s.executor.bind_dedicated(clear_stop=False)
        self.check(epoch)
        if self.input_stopped():
            raise LobbyError('input_stopped_before_room_start')
        if (session.generation!=generation or not session.room or session.room['room_code']!=command.room_code
                or session.room.get('playing')):
            raise LobbyError('room_changed_before_start')
        if s.engine.state and s.engine.state.lifecycle=='active':
            raise LobbyError('game_started_before_room_start')
        if existing:
            if self.arm_id!=existing or self.arm_task.done():
                raise LobbyError('autoplay_authorization_expired_before_start')
            cid=existing
            record=self.get(cid)
        else:
            cid='room-arm-'+hashlib.sha256(command.command_id.encode()).hexdigest()[:32]
            authorization=OperationRequest(command_id=cid,command={'action':'arm','seconds':600})
            # Internal child authorization has a durable pointer; never resubmit
            # or revive the same id after a restart or an uncertain room action.
            try:
                self.get(cid)
            except KeyError:
                pass
            else:
                raise LobbyError('room_start_authorization_already_consumed')
            record={'request':authorization.model_dump(mode='json'),'status':'running','created_at':utcnow(),
                    'updated_at':utcnow(),'result':None,'reason':None}
            s.store.db.execute('INSERT INTO operation_commands VALUES(?,?)',(cid,packed(record)))
            s.store.db.commit()
            result=await self.arm(authorization,epoch)
            record=self.update(cid,status='waiting_game',result=result)
        self.update(cid,result={**(record.get('result') or {}),'room_code':command.room_code,
                               'prepared_before_start':True})
        s.store.record('room_takeover_prepared',{'command_id':command.command_id,'authorization_id':cid})
        return cid

    async def cancel_room_start(self, cid, reason):
        if self.arm_id==cid:
            await self.revoke_arm(reason)

    async def room_start_result(self, cid, result):
        if result['status']=='rejected':
            await self.cancel_room_start(cid,'room_start_rejected')
        elif result['status']=='confirmed' and self.arm_id==cid:
            # Only observe the already-installed task; this never grants input.
            try:
                await asyncio.wait_for(asyncio.shield(self.arm_task),35)
            except TimeoutError:
                pass
        record=self.get(cid)
        active=record['status']=='succeeded' and self.s.coordinator.enabled
        return {**result,'takeover':'active' if active else 'waiting_for_fresh_game'
                    if record['status']=='waiting_game' else record['status'],
                'autoplay_prepared':record['status']=='waiting_game',
                'authorization_status':record['status']}

    def startup(self):
        """Public startup guidance, without hands, strategy or private settings."""
        if self.s.coordinator.enabled:
            return {'phase':'shadow' if self.s.coordinator.shadow else 'active','reason':None}
        if self.arm_id and self.arm_task and not self.arm_task.done():
            record=self.get(self.arm_id)
            return {'phase':(record.get('result') or {}).get('stage','awaiting_new_game'),
                    'reason':None,'command_id':self.arm_id,'one_match_only':True,'autoplay_prepared':True,
                    'shadow':bool((record.get('result') or {}).get('shadow',
                        record['request']['command'].get('shadow',record['request']['command']['action']=='shadow')))}
        reason=self.game_blocker()
        return {'phase':'stopped','reason':reason,
                'recovery':STARTUP_RECOVERY.get(reason), 'context_ready':reason is None}

    def catalog(self):
        try:
            rooms=sorted(self.s.lobby_ui.profile()['scenes']['room']['controls'])
        except (OSError,ValueError,KeyError,LobbyError):
            rooms=[]
        return {'schema_version':'mahjong.operations.v1','command_schema':OperationRequest.model_json_schema(),
                'resources':['status','catalog','settings','profiles','usage','review_jobs','yaku','calibration','diagnostics','command','knowledge','learning'],
                'navigation':['login','ranked','join_friend','create_friend'],
                'query':['live','matches','rounds','big-hands','memories'],
                'limitations':['login_credentials_and_captcha_may_require_user',
                               'visible_cancel_match_adapter_pending','four_player_standard_rules_only',
                               'verification_flags_are_observations_not_switches'],
                'calibrated_room_controls':rooms,'screenshot':'view/screenshot; idle only',
                'autoplay_start_order':['bind_dedicated_window','persist_one_match_authorization',
                                        'click_room_start','observe_complete_game','automatic_handoff'],
                'pre_game_start_prepares_autoplay':True,
                'ai_room_opponents_supplied_by_game':True,
                'new_match_automatically_started':False}

    def resource(self, name, *, command_id=None, ids=None, topic=''):
        if name == 'status':
            return {**self.status(),'startup':self.startup()}
        if name == 'catalog':
            return self.catalog()
        if name == 'settings':
            return self.settings()
        if name == 'knowledge':
            return Knowledge(self.s.store).read(query=topic,ids=ids or [])
        if name == 'learning':
            return {**learning_status(self.s.store),'recall_enabled':self.s.settings.memory_recall_enabled,
                    'shared_recall_limit':self.s.settings.memory_recall_limit}
        if name == 'profiles':
            return self.profiles()
        if name == 'command':
            return self.get(command_id)
        if name == 'usage':
            result = usage_summary(self.s.store,self.s.client.credential_id)
            return {'billing_scope':result['billing_scope'],'totals_are_known_usage_only':True,
                    'groups':[{key:value for key,value in group.items() if key != 'credential_id'} for group in result['groups']]}
        if name == 'review_jobs':
            latest = {}
            for row in self.s.store.records('review_job',limit=100):
                latest.setdefault(row['match_id'],{'match_id':row['match_id'],**row['payload']})
            return list(latest.values())[:20]
        if name == 'yaku':
            return YakuReference().read(ids=ids or [],topic=topic,all_entries=not ids and not topic)
        if name == 'calibration':
            folder = self.s.settings.runtime/'calibration'
            return {'labels':sorted(p.stem for p in folder.glob('*.png')),'screenshots_are_local':True}
        if name == 'diagnostics':
            s=self.s
            return {'connected':s.browser.connected,'protocol_loaded':bool(s.engine.protocol),
                    'capture_health':s.engine.capture_health(),'recovery':s.coordinator.recovery.public(),
                    'recent_browser_error_types':[error for error in s.browser.errors[-5:]
                        if isinstance(error,str) and error.replace('_','').isalnum()],
                    'runtime_build':s.runtime_build}
        raise KeyError(name)

    async def submit(self, request):
        command = request.command
        data = request.model_dump(mode='json')
        try:
            previous = self.get(request.command_id)
        except KeyError:
            previous = None
        if previous:
            if previous['request'] != data:
                raise LobbyError('command_id_content_conflict')
            return previous
        stopping = command.action in ('pause','stop','emergency')
        if self.busy() and not stopping:
            raise LobbyError('operation_busy_query_original_command')
        record = {'request':data,'status':'queued','created_at':utcnow(),'updated_at':utcnow(),
                  'result':None,'reason':None}
        self.s.store.db.execute('INSERT INTO operation_commands VALUES(?,?)',(request.command_id,packed(record)))
        self.s.store.db.commit()
        if stopping:
            await self.stop(command.action)
            return self.update(request.command_id,status='succeeded',result={'enabled':False,'waiting_game':False})
        self.task = asyncio.create_task(self.run(request,self.epoch))
        return record

    async def revoke_arm(self, reason='stopped'):
        self.epoch += 1
        if self.arm_task and not self.arm_task.done():
            self.arm_task.cancel()
            await asyncio.gather(self.arm_task,return_exceptions=True)
        if self.arm_id:
            record = self.get(self.arm_id)
            if record['status'] == 'waiting_game':
                self.update(self.arm_id,status='cancelled',reason=reason)
        self.arm_task = None
        self.arm_id = None

    async def stop(self, reason='stop'):
        self.s.executor.mouse.stop.set()
        await self.revoke_arm(reason)
        if self.busy() and self.task is not asyncio.current_task():
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
        await self.s.navigator.stop()
        await self.s.round_flow.stop()
        await self.s.client_guard.stop()
        await self.s.coordinator.stop(reason)

    async def close(self):
        await self.stop('service_stopped')

    def check(self, epoch):
        if epoch != self.epoch:
            raise LobbyError('operation_superseded_by_stop')

    async def run(self, request, epoch):
        self.update(request.command_id,status='running')
        try:
            result = await self.execute(request,epoch)
            if result.get('waiting_game'):
                self.update(request.command_id,status='waiting_game',result=result)
            else:
                status={'uncertain':'uncertain','rejected':'failed','submitted':'uncertain'}.get(result.get('status'),'succeeded')
                self.update(request.command_id,status=status,result=result)
        except asyncio.CancelledError:
            submitted = request.command.action == 'room' and any(
                row['payload']['request']['command_id'] == request.command_id
                for row in self.s.store.records('room_command',limit=1000))
            self.update(request.command_id,status='uncertain' if submitted else 'cancelled',
                        reason='stopped_after_submission_check_actual_state' if submitted else 'stopped')
            raise
        except Exception as error:
            # Never publish exception text, profile fields, model text or stack.
            code = error.code if isinstance(error,LobbyError) else 'operation_unavailable_'+type(error).__name__.lower()
            self.update(request.command_id,status='failed',reason=code,
                        result={'recovery':STARTUP_RECOVERY[code]} if code in STARTUP_RECOVERY else None)

    def game_blocker(self):
        s=self.s
        state=s.engine.state
        if not s.browser.connected:
            return 'browser_not_connected'
        if not state or state.lifecycle != 'active':
            return 'active_game_required_use_arm'
        if not s.engine.authenticated_mono:
            return 'authenticated_game_required'
        if state.rules.unsupported:
            return 'unsupported_game_rules'
        if not state.consistent:
            return 'inconsistent_game_state'
        if state.round_completeness != 'full' or not state.round_uid:
            return 'complete_game_state_required'
        return None

    def ready_game(self):
        s = self.s
        state = s.engine.state
        reason=self.game_blocker()
        if reason:
            raise LobbyError(reason)
        if state.operation and (state.operation.historical or state.operation.received_mono < s.engine.authenticated_mono):
            raise LobbyError('fresh_action_window_required')
        if state.operation:
            # Validate against the existing pool; never create a fresh deadline.
            op=state.operation
            if s.settings.integration_test and op.fixed_ms and op.fixed_ms >= 300000 and op.add_ms == 0:
                if time.monotonic() >= op.received_mono+op.fixed_ms/1000-s.settings.execution_margin:
                    raise LobbyError('action_deadline_expired')
            else:
                import copy
                bank=copy.deepcopy(s.coordinator.bank)
                bank.enter_round(state.round_uid)
                prior=next((row['payload'] for row in s.store.records('time_bank',state.match_id,1000)
                            if row['round_uid'] == state.round_uid),None)
                if prior:
                    bank.restore(prior)
                try:
                    bank.deadline(op,verified=s.settings.timer_verified)
                except TimeoutError:
                    raise LobbyError('action_deadline_expired') from None
        return state

    def input_stopped(self):
        s=self.s
        user=getattr(s.executor.mouse,'user',None)
        if user and user.GetAsyncKeyState(0x1B)&0x8000:
            s.executor.mouse.stop.set()
        return s.executor.mouse.stop.is_set()

    async def start_game(self, shadow, epoch, *, from_arm=False, deadline=None):
        self.idle()
        before = self.ready_game()
        generation=self.s.engine.lobby_session.generation
        authentication=self.s.engine.authenticated_mono
        if not from_arm:
            self.s.executor.mouse.stop.clear()
        if self.input_stopped():
            raise LobbyError('input_stopped_before_handoff')
        await self.s.executor.bind_dedicated(clear_stop=False)
        self.check(epoch)
        if self.input_stopped():
            raise LobbyError('input_stopped_during_handoff')
        current = self.ready_game()
        if (self.s.engine.lobby_session.generation != generation
                or self.s.engine.authenticated_mono != authentication):
            raise LobbyError('authentication_changed_during_binding')
        if current.match_id != before.match_id:
            raise LobbyError('match_changed_during_binding')
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('wait_for_game_expired')
        await self.s.coordinator.start(shadow=shadow)
        return {'enabled':self.s.coordinator.enabled,'shadow':shadow,'match_id':current.match_id,
                'status':self.s.coordinator.status}

    async def arm(self, request, epoch):
        self.idle()
        session = self.s.engine.lobby_session
        if not self.s.browser.connected or not session.authenticated:
            raise LobbyError('authenticated_browser_required_before_arm')
        if self.arm_task and not self.arm_task.done():
            raise LobbyError('existing_wait_for_game')
        state=self.s.engine.state
        current_match=state.match_id if state and state.lifecycle == 'active' else None
        if current_match:
            try:
                self.ready_game()
            except LobbyError as error:
                if error.code not in WAITABLE_STARTUP:
                    raise
            else:
                return await self.start_game(request.command.shadow,epoch)
        self.s.executor.mouse.stop.clear()
        self.arm_id = request.command_id
        started=time.monotonic()
        self.arm_task = asyncio.create_task(self.wait_game(request,epoch,session.generation,started,current_match,
            state.match_id if state else None))
        return {'waiting_game':True,'seconds':request.command.seconds,'one_match_only':True,
                'autoplay_prepared':True,'enabled':False,'shadow':request.command.shadow,
                'stage':'awaiting_complete_round' if current_match else 'awaiting_new_game'}

    async def wait_game(self, request, epoch, generation, started, current_match, old_match):
        s = self.s
        target_match=current_match
        deadline=started+request.command.seconds
        try:
            while time.monotonic() < deadline:
                self.check(epoch)
                if (self.input_stopped() or not s.browser.connected
                        or s.engine.lobby_session.generation != generation):
                    raise LobbyError('wait_authorization_revoked')
                state = s.engine.state
                bound_room=(self.get(request.command_id).get('result') or {}).get('room_code')
                if bound_room and (not s.engine.lobby_session.room or
                        s.engine.lobby_session.room['room_code']!=bound_room):
                    raise LobbyError('room_changed_while_waiting')
                if (state and state.lifecycle == 'active' and s.engine.authenticated_mono
                        and (state.match_id == current_match or
                             state.match_id != old_match and s.engine.authenticated_mono >= started)):
                    if target_match and state.match_id != target_match:
                        raise LobbyError('match_changed_while_waiting')
                    target_match=state.match_id
                    try:
                        self.ready_game()
                    except LobbyError as error:
                        if error.code not in WAITABLE_STARTUP:
                            raise
                        stage=('awaiting_complete_round' if error.code=='complete_game_state_required'
                               else 'awaiting_fresh_action')
                    else:
                        stage='awaiting_navigation_idle' if s.navigator.busy() or s.input_lease.owner else 'binding'
                    record=self.get(request.command_id)
                    if (record.get('result') or {}).get('stage') != stage:
                        self.update(request.command_id,result={**(record.get('result') or {}),'stage':stage})
                    if stage == 'binding':
                        try:
                            result=await asyncio.wait_for(self.start_game(request.command.shadow,epoch,from_arm=True,
                                deadline=deadline),
                                max(.001,deadline-time.monotonic()))
                        except TimeoutError:
                            break
                        except LobbyError as error:
                            if error.code not in WAITABLE_STARTUP:
                                raise
                            # Binding can outlast the current action. It has
                            # sent no input; wait for fresh facts under the same
                            # original authorization rather than failing/reusing it.
                            self.update(request.command_id,result={**(record.get('result') or {}),
                                'stage':'awaiting_fresh_action'})
                        else:
                            self.update(request.command_id,status='succeeded',result=result)
                            return
                await asyncio.sleep(.1)
            self.update(request.command_id,status='expired',reason='wait_for_game_expired')
        except asyncio.CancelledError:
            self.update(request.command_id,status='cancelled',reason='stopped')
            raise
        except Exception as error:
            self.update(request.command_id,status='failed',reason=error.code if isinstance(error,LobbyError) else 'game_handoff_failed')
        finally:
            if self.arm_id == request.command_id:
                self.arm_id = None

    async def execute(self, request, epoch):
        s = self.s
        c = request.command
        action = c.action
        self.check(epoch)
        if action in ('start','shadow'):
            state=s.engine.state
            if not state or state.lifecycle!='active':
                if self.arm_id:
                    return {'autoplay_prepared':True,'enabled':False,
                            'authorization_command_id':self.arm_id,'stage':'awaiting_new_game'}
                authorization=request.model_copy(update={'command':ArmCommand(action='arm',shadow=action=='shadow')})
                return await self.arm(authorization,epoch)
            if self.arm_id:
                await self.revoke_arm('explicit_start_replaces_wait')
                epoch = self.epoch
            return await self.start_game(action == 'shadow',epoch)
        if action == 'arm':
            return await self.arm(request,epoch)
        if action == 'confirm_results':
            await s.round_flow.resume()
            return {'result_flow_started':True,'enabled':False,'result':'await_visible_confirmation'}
        if action == 'room':
            room=s.engine.lobby_session.room
            if c.operation.startswith('add_robot') and room and room.get('mode',{}).get('mode')==3:
                raise LobbyError('ai_room_fills_opponents_on_start')
            result = await PracticeRooms(s,solo_only=False).run(RoomAction(command_id=request.command_id,room_code=c.room_code,
                action=c.operation,takeover=c.takeover or c.operation=='start'))
            return {key:value for key,value in result.items() if key != 'request'}
        if action == 'review_enqueue':
            state = s.store.latest(c.match_id)
            if not state or state.lifecycle == 'active':
                raise LobbyError('ended_or_interrupted_recorded_match_required')
            await s.reviewer.enqueue(c.match_id)
            return {'match_id':c.match_id,'queued':c.match_id in s.reviewer.queued,
                    'auto_review_enabled':s.settings.review_enabled}
        if action == 'review_cancel':
            if not s.store.latest(c.match_id):
                raise KeyError(c.match_id)
            await s.reviewer.cancel(c.match_id)
            return {'match_id':c.match_id,'review_cancelled':True}
        if action == 'memory_revise':
            proposal = c.proposal.model_dump(exclude_none=True)
            memory = Memories(s.store).revise(proposal,c.match_id)
            return {key:memory[key] for key in ('id','revision','status')}
        if action == 'knowledge_write':
            memory=Knowledge(s.store).write(c.proposal,source_quote=c.source_quote,source_ref=request.command_id)
            return {'knowledge':memory,'effective':'next_decision_snapshot',
                    'recall_enabled':s.settings.memory_recall_enabled}
        if action == 'memory_disable':
            memory=Memories(s.store).disable(c.id,c.revision,c.reason)
            s.store.record('memory_admin_disable',{'id':c.id,'revision':memory['revision'],
                'source_ref':request.command_id,'source_digest':hashlib.sha256(c.source_quote.encode()).hexdigest()})
            return {key:memory[key] for key in ('id','revision','status')}
        if self.arm_id:
            raise LobbyError('cancel_wait_before_reconfiguration')
        self.idle(no_match=action in ('profile_select','profile_capture','service_stop','browser_reconnect','calibrate'))
        if action == 'input_mode':
            return await self.configure({'input_mode':c.mode})
        if action == 'settings':
            patch = c.patch.model_dump(exclude_none=True)
            if not patch:
                raise LobbyError('empty_settings_patch')
            return await self.configure(patch)
        if action in ('profile_select','profile_capture'):
            return await self.profile(c)
        if action == 'browser_open':
            await s.browser.start()
        elif action == 'browser_reconnect':
            if not s.browser.connected:
                # Probe only the configured CDP endpoint, then verify the process/profile.
                from playwright.async_api import async_playwright
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{s.settings.debug_port}')
                    session = await browser.new_browser_cdp_session()
                    try:
                        processes = (await session.send('SystemInfo.getProcessInfo'))['processInfo']
                        pid = next(int(p['id']) for p in processes if p['type'] == 'browser')
                    finally:
                        await session.detach()
                        await browser.close()
                await s.browser.attach_existing(pid)
        elif action == 'bind':
            await s.executor.bind_dedicated()
        elif action == 'fit_window':
            await s.browser.fit_window()
        elif action == 'calibrate':
            if any(v < 0 for v in c.box) or c.box[2] <= 0 or c.box[3] <= 0:
                raise LobbyError('invalid_calibration_rectangle')
            s.executor.locator.save_crop(await s.browser.image_bytes(),c.label,list(c.box),
                                         hand_region=list(c.hand_region) if c.hand_region else None)
            return {'calibrated':c.label}
        elif action == 'service_stop':
            shutdown = getattr(s,'request_shutdown',None)
            if not shutdown:
                raise LobbyError('service_shutdown_adapter_unavailable')
            await self.stop('service_stop')
            await s.reviewer.pause_for_decision()
            asyncio.get_running_loop().call_later(.5,shutdown)
            return {'shutdown_scheduled':True,'enabled':False}
        else:
            raise LobbyError('unknown_operation')
        return {'completed':action,'connected':s.browser.connected,'window_bound':bool(s.executor.window)}

    async def configure(self, patch):
        s = self.s
        for gate in MODEL_GATES:
            if patch.get(gate) and not self.permissions.get(gate):
                raise LobbyError('format_requires_existing_local_validation')
        chosen = Settings.model_validate({**s.settings.model_dump(),
            **{key:getattr(s.settings,key) for key in ('api_key','control_token','read_token','navigation_token','operation_token')},**patch})
        if patch.get('review_enabled') is False:
            await s.reviewer.pause_for_decision()
        if patch.get('input_mode') == 'physical_foreground':
            await s.browser.release_background()
        chosen.save_private()
        for key in patch:
            setattr(s.settings,key,getattr(chosen,key))
        if any(key in patch for key in ('base_seconds','bank_seconds','execution_margin')):
            bank = s.coordinator.bank
            bank.base,bank.initial,bank.margin=chosen.base_seconds,chosen.bank_seconds,chosen.execution_margin
            # Editing configuration cannot refill the already observed pool.
            bank.remaining=min(bank.remaining,chosen.bank_seconds)
        if 'input_mode' in patch:
            s.executor.input_mode=chosen.input_mode
        if patch.get('review_enabled') is True:
            await s.reviewer.resume_pending()
        return {'applied':list(patch),'persistent':True,'settings':self.settings()}

    async def profile(self, command):
        s = self.s
        if (s.reviewer.queued or s.reviewer.review_task and not s.reviewer.review_task.done()
                or s.client.streams or s.client.races or s.coordinator.annotation_tasks):
            raise LobbyError('model_requests_or_reviews_active')
        profiles = ModelProfiles(s.settings)
        if command.action == 'profile_capture':
            name = 'saved_' + hashlib.sha256(utcnow().encode()).hexdigest()[:16]
            profiles.capture(name)
            return {'captured_profile_id':profile_id(name)}
        book = profiles.load()
        name = next((name for name in book.profiles if profile_id(name) == command.profile_id),None)
        if name is None:
            raise ProfileError('unknown local profile')
        chosen = book.profiles[name].settings(s.settings,name)
        import os
        if os.getenv('MAJSOUL_API_KEY') and os.environ['MAJSOUL_API_KEY'] != chosen.api_key.get_secret_value():
            raise ProfileError('environment key override differs')
        client = ModelClient(chosen,s.store)
        try:
            chosen.save_private()
        except BaseException:
            await client.close()
            raise
        old = s.client
        for key in Settings.model_fields:
            setattr(s.settings,key,getattr(chosen,key))
        client.settings=s.settings
        s.client=s.coordinator.model=s.reviewer.client=client
        self.permissions=self._format_permissions()
        await old.close()
        return {'selected_profile_id':command.profile_id,'applied':True,'restart_required':False}
