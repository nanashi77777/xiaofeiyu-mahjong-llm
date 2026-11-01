"""Drive authorized solo AI matches through the production HTTP service only.

Reports remain private under runtime. Strict runs stop on any failure. Explicit
recovery runs retain failures and are excluded from strict performance acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path

import httpx

from majsoul_llm.config import ROOT, Settings
from majsoul_llm.store import Store, utcnow
from majsoul_llm.validation import match_report


class PracticeSuite:
    def __init__(self, args, settings, client):
        self.args, self.settings, self.client = args, settings, client
        self.started = time.monotonic()
        self.recovery_validation = getattr(args,'recovery_validation',False)
        self.functional_validation = getattr(args,'functional_validation',False)
        if self.recovery_validation and self.functional_validation:
            raise ValueError('validation modes are mutually exclusive')
        self.report = {'schema_version':'practice-suite.v1', 'started_at':utcnow(),
                       'profile':args.profile, 'kind':'production_live_websocket',
                       'acceptance_eligible':args.profile == '5+10', 'matches':[],
                       'status':'running', 'all_failures_retained':True}
        self.owns_gameplay = False
        self.navigation_id = None
        if self.recovery_validation:
            self.report.update(kind='production_recovery_validation',acceptance_eligible=False,
                               recovery_validation=True)
        if self.functional_validation:
            self.report.update(kind='production_functional_validation',acceptance_eligible=False,
                               validation_scope='functional_only',performance_gate=False)

    def save(self):
        self.args.output.parent.mkdir(parents=True, exist_ok=True)
        self.args.output.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding='utf-8')

    def check(self):
        if self.args.stop_file.exists():
            raise RuntimeError('stop_file_received')
        if time.monotonic()-self.started > self.args.seconds:
            raise TimeoutError('suite_session_limit')

    async def status(self):
        self.check()
        response = await self.client.get('/control/progress')
        response.raise_for_status()
        return response.json()

    async def post(self, path, body):
        self.check()
        response = await self.client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    async def room(self, code, action, **extra):
        result = await self.post('/control/room', {'command_id':'suite-'+uuid.uuid4().hex,
                                 'room_code':code, 'action':action, **extra})
        if result['status'] not in ('confirmed','dialog_opened'):
            raise RuntimeError('room_command_'+result['status'])
        return result

    async def prepare_room(self):
        status = await self.status()
        if status['enabled'] or status['navigation']['active_command']:
            raise RuntimeError('existing_input_owner')
        if status.get('state') and status['state']['lifecycle'] == 'active':
            raise RuntimeError('existing_active_match')
        expected = hashlib.sha256(b''.join(p.read_bytes() for p in sorted((ROOT/'src/majsoul_llm').glob('*.py')))).hexdigest()
        if status.get('runtime_build',{}).get('source_digest') != expected:
            raise RuntimeError('service_source_changed_restart_required')
        self.report['runtime_build'] = status['runtime_build']
        self.report['timer_verified'] = status['timer_verified']
        if self.args.profile == '5+10' and not status['timer_verified']:
            raise RuntimeError('real_timer_calibration_required')
        login = status['login_session']
        if not login['authenticated'] or login['has_game'] or login['matching']:
            raise RuntimeError('authenticated_idle_session_required')
        fixed, bank = map(int, self.args.profile.split('+'))
        room = login.get('room')
        wanted = {'time_fixed':fixed, 'time_add':bank}
        if room and (room['mode'].get('mode') != 2 or room['mode'].get('detail_rule') != wanted or room['players'] != 4):
            result = await self.room(room['room_code'], 'leave')
            if result['status'] == 'dialog_opened':
                await self.room(room['room_code'], 'confirm_leave')
            if (await self.status())['login_session']['room']:
                raise RuntimeError('room_leave_not_observed')
            room = None
        if room is None:
            command = {'command_id':'suite-'+uuid.uuid4().hex, 'kind':'create_friend', 'players':4,
                       'length':'south', 'thinking':{'fixed_seconds':fixed,'bank_seconds':bank}}
            self.navigation_id = command['command_id']
            await self.post('/api/v1/mahjong/navigation/commands', command)
            for _ in range(240):
                progress = (await self.client.get('/api/v1/mahjong/navigation/commands/'+command['command_id'])).json()
                if progress['status'] == 'succeeded':
                    break
                if progress['status'] not in ('queued','running'):
                    raise RuntimeError('create_room_'+progress['status'])
                await asyncio.sleep(.25)
                self.check()
            else:
                raise TimeoutError('create_room_confirmation_missing')
            room = (await self.status())['login_session']['room']
            self.navigation_id = None
        if not room or room['players'] != 4 or room['humans'] != 1 or room['mode'].get('mode') != 2 or room['mode'].get('detail_rule') != wanted:
            raise RuntimeError('room_rules_or_single_human_not_verified')
        if room.get('robots') == 0:
            for slot in (1,2,3):
                await self.room(room['room_code'], 'add_robot_'+str(slot))
        elif room['robots'] != 3:
            raise RuntimeError('partial_robot_room_requires_inspection')
        room = (await self.status())['login_session']['room']
        if room['humans'] != 1 or room['robots'] != 3 or room['playing']:
            raise RuntimeError('one_human_three_robots_not_verified')
        self.report['room'] = room
        return room['room_code']

    async def run(self):
        store = Store(self.settings.runtime/'archive.sqlite')
        match_id = None
        try:
            code = await self.prepare_room()
            for index in range(self.args.games):
                self.owns_gameplay = True
                result = await self.room(code, 'start', takeover=True)
                if result.get('takeover') != 'active':
                    raise RuntimeError('fresh_match_takeover_unconfirmed')
                match_id = result['match_id']
                self.report['matches'].append({'match_id':match_id,'status':'active'})
                print(json.dumps({'game':index+1,'match_id':match_id,'status':'started'}), flush=True)
                last_count = -1
                report_revision = None
                while True:
                    status = await self.status()
                    state = status.get('state')
                    if not state or state['match_id'] != match_id:
                        raise RuntimeError('match_identity_changed')
                    revision = (state['state_version'],status['records_revision'])
                    if revision != report_revision or state['lifecycle'] == 'ended':
                        summary = match_report(store, match_id)
                        report_revision = revision
                        self.report['matches'][-1] = summary
                        self.save()
                    if not self.recovery_validation and (summary['failures'] or summary['mismatches'] or summary['client_interventions']
                            or any(x['window_id'] in summary['windows'] for x in summary['client_automatic_inputs'])):
                        raise RuntimeError('recorded_decision_or_execution_failure')
                    if self.recovery_validation and summary['mismatches']:
                        raise RuntimeError('recovery_input_mismatch')
                    if (not state['consistent'] or state['completeness'] != 'full'
                            or state['lifecycle'] == 'interrupted' and not self.recovery_validation):
                        raise RuntimeError('state_gap_or_interruption')
                    if summary['opportunities'] != last_count:
                        last_count = summary['opportunities']
                        print(json.dumps({'game':index+1,'round':state['round_seq'],
                                          'opportunities':last_count,'confirmed':summary['server_confirmed']}), flush=True)
                    if state['lifecycle'] == 'ended':
                        if not summary['final_scores'] or not summary['final_ranks']:
                            raise RuntimeError('final_result_missing')
                        break
                    if not status['enabled']:
                        raise RuntimeError('production_coordinator_stopped')
                    await asyncio.sleep(.5)
                deadline = time.monotonic()+60
                while True:
                    status = await self.status()
                    room = status['login_session'].get('room')
                    flow = status.get('result_flow',{})
                    if (room and room['room_code'] == code and not room['playing']
                            and not flow.get('active') and flow.get('last_scene') == 'room'):
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError('final_visible_confirmation_incomplete')
                    await asyncio.sleep(.25)
                summary = match_report(store, match_id)
                self.report['matches'][-1] = summary
                if not self.recovery_validation and (summary['server_confirmed'] != summary['opportunities'] or any(w['decision_count'] != 1 for w in summary['windows'].values())):
                    raise RuntimeError('unconfirmed_or_repeated_model_opportunity')
                if any(w['decision_count'] > 1 for w in summary['windows'].values()):
                    raise RuntimeError('repeated_model_opportunity')
                if not any(r.get('scene') == 'final' and r.get('screenshot') for r in summary['result_evidence']):
                    raise RuntimeError('final_screenshot_evidence_missing')
                print(json.dumps({'game':index+1,'status':'complete','scores':summary['final_scores'],
                                  'ranks':summary['final_ranks']}), flush=True)
                self.save()
                match_id = None
            self.report['status'] = 'completed'
        except (Exception, asyncio.CancelledError) as error:
            self.report.update(status='stopped',error_type=type(error).__name__,
                               reason=str(error) if isinstance(error,(RuntimeError,TimeoutError)) else None)
            if match_id:
                self.report['matches'][-1] = match_report(store, match_id)
            try:
                screenshot = await self.client.get('/control/screenshot')
                if screenshot.is_success:
                    evidence = self.args.output.with_suffix('.png')
                    evidence.write_bytes(screenshot.content)
                    self.report['failure_screenshot'] = evidence.name
            except Exception:
                pass
            raise
        finally:
            try:
                if self.owns_gameplay:
                    await self.client.post('/control/stop', json={})
                elif self.navigation_id:
                    await self.client.post('/api/v1/mahjong/navigation/commands/'+self.navigation_id+'/cancel', json={})
            finally:
                self.report.update(finished_at=utcnow(),elapsed_seconds=time.monotonic()-self.started)
                self.save()
                store.close()


async def main(args):
    settings = Settings.load()
    async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{settings.port}',trust_env=False,timeout=60,
                                 headers={'Authorization':'Bearer '+settings.control_token.get_secret_value()}) as client:
        suite = PracticeSuite(args, settings, client)
        try:
            await suite.run()
        except Exception as error:
            print(json.dumps({'status':suite.report['status'],'error_type':type(error).__name__,
                              'reason':suite.report.get('reason')}), flush=True)
            return 1
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--games',type=int,default=3)
    parser.add_argument('--seconds',type=float,default=14400)
    parser.add_argument('--profile',choices=('300+0','60+0','5+10'),default='300+0')
    parser.add_argument('--output',type=Path,default=Path('runtime/practice-suite.json'))
    parser.add_argument('--stop-file',type=Path,default=Path('runtime/STOP-PRACTICE'))
    parser.add_argument('--recovery-validation',action='store_true',help='Retain recoverable failures; excluded from strict acceptance')
    parser.add_argument('--functional-validation',action='store_true',help='Independent functional run; timing retained without performance qualification')
    args = parser.parse_args()
    if args.games < 1 or args.seconds <= 0:
        parser.error('positive games and session seconds required')
    if args.functional_validation and args.recovery_validation:
        parser.error('select only one validation mode')
    raise SystemExit(asyncio.run(main(args)))
