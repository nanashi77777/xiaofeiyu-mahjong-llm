import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from majsoul_llm.config import Settings


spec = importlib.util.spec_from_file_location('functional_suite',Path(__file__).parents[1]/'tools/practice_suite.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('profile',['5+10','60+0','300+0'])
async def test_functional_reports_never_gain_performance_qualification(profile,tmp_path):
    args = SimpleNamespace(profile=profile,functional_validation=True,output=tmp_path/'report.json',
                           stop_file=tmp_path/'STOP',seconds=1)
    suite = module.PracticeSuite(args,Settings(),None)
    suite.save()
    report = json.loads(args.output.read_text(encoding='utf-8'))
    assert not report['acceptance_eligible'] and report['validation_scope'] == 'functional_only'
    assert report['all_failures_retained'] and not report['performance_gate']


async def test_functional_mode_keeps_real_input_failure_and_stops_gameplay(tmp_path,monkeypatch):
    args = SimpleNamespace(profile='60+0',functional_validation=True,output=tmp_path/'report.json',
                           stop_file=tmp_path/'STOP',seconds=10,games=1)
    calls = []
    summary = {'match_id':'synthetic','opportunities':1,'failures':[],
               'mismatches':[{'reason':'synthetic_actual_tile_differs'}],'client_interventions':[],
               'client_automatic_inputs':[],'windows':{}}
    monkeypatch.setattr(module,'match_report',lambda *args:summary)
    monkeypatch.setattr(module,'Store',lambda path:SimpleNamespace(close=lambda:None))
    def handler(request):
        calls.append((request.method,request.url.path))
        if request.url.path == '/control/status':
            return httpx.Response(200,json={'enabled':True,'state':{'match_id':'synthetic','lifecycle':'active'}})
        return httpx.Response(200,content=b'fixture')
    async with httpx.AsyncClient(base_url='http://fixture',transport=httpx.MockTransport(handler)) as client:
        suite = module.PracticeSuite(args,Settings(runtime=tmp_path),client)
        async def prepare():
            return '00000'
        async def room(*args,**kwargs):
            return {'takeover':'active','match_id':'synthetic'}
        suite.prepare_room,suite.room = prepare,room
        with pytest.raises(RuntimeError,match='recorded_decision_or_execution_failure'):
            await suite.run()
    report = json.loads(args.output.read_text(encoding='utf-8'))
    assert report['status'] == 'stopped' and report['matches'][0]['mismatches']
    assert ('POST','/control/stop') in calls
    assert not report['acceptance_eligible']
