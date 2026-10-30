import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import event
from majsoul_llm.app import create_app
from majsoul_llm.config import Settings
from majsoul_llm.memory import Memories
from majsoul_llm.model import ModelClient, ModelFailure
from majsoul_llm.queries import Queries
from majsoul_llm.rules import legal_actions
from majsoul_llm.state import apply_event
from majsoul_llm.wire import build_context


def test_query_active_private_text_hidden(store,state):
    store.save_event(event("ActionNewRound", {"tiles":state.hand}),state)
    store.record("decision",{"decision":{"reason":"暗手1m对，准备混一色","plan_update":{"goal":"暗手策略"}}},state.match_id,state.round_uid)
    q=Queries(store,"testing-secret")
    active=q.round(state.round_uid)
    encoded=json.dumps(active,ensure_ascii=False)
    assert "暗手策略" not in encoded and "暗手1m" not in encoded and '"tiles"' not in encoded
    assert active["data"]["decisions"] == []
    historical=apply_event(state,event("ActionHule",{"hules":[{"seat":0,"count":6,"fu":30,"point_sum":12000}],"scores":[37000,21000,21000,21000]},2))
    store.save_event(event("ActionHule",{"hules":[{"seat":0,"count":6,"fu":30,"point_sum":12000}]},2),historical)
    assert "暗手1m" in json.dumps(q.round(state.round_uid),ensure_ascii=False)
    scope=q.matches()["scope_cursor"]
    assert q.big_hands(scope)["data"][0]["level"]=="haneman"
    with pytest.raises(ValueError):
        q.big_hands(scope[:-2]+"XX")
    assert q.match(state.match_id)["data"]["final_result"] is None


def test_http_read_cannot_control_and_origin(store):
    settings=Settings()
    service=SimpleNamespace(queries=Queries(store,"test"),status=lambda:{"ok":True})
    app=create_app(settings,service)
    client=TestClient(app)
    read={"Authorization":"Bearer "+settings.read_token.get_secret_value()}
    control={"Authorization":"Bearer "+settings.control_token.get_secret_value()}
    assert client.get("/api/v1/mahjong/live",headers=read).status_code==200
    assert client.get("/control/status",headers=read).status_code==401
    assert client.get("/control/status",headers=control).status_code==200
    assert client.post("/control/stop",json={},headers={**control,"Origin":"https://example.com"}).status_code==403
    assert client.get("/api/v1/mahjong/matches?limit=100",headers=read).status_code==422
    assert client.get("/control/status").status_code==401


async def test_model_strict_terminal_and_usage(state,store):
    context=build_context(state,legal_actions(state))
    output={"q":context.q,"action_id":"a0","reason":"保留进张","plan_update":None}
    seen=[]
    def handler(request):
        body=json.loads(request.content)
        seen.append(body)
        chunks=[{"type":"response.output_text.delta","delta":json.dumps(output,ensure_ascii=False)},
                {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":100,"output_tokens":30,"total_tokens":130}}}]
        return httpx.Response(200,text="".join('data: '+json.dumps(x,ensure_ascii=False)+'\n\n' for x in chunks))
    c=ModelClient(Settings(json_schema=True),store,httpx.MockTransport(handler))
    raw,metrics=await c.request(context.data)
    assert context.validate(raw)[1].kind=="discard" and metrics["usage"]["total_tokens"]==130
    assert seen[0]["reasoning"]=={"effort":"none"} and not seen[0]["store"]
    assert "RoundPlanUpdate" in seen[0]["text"]["format"]["schema"]["$defs"]
    await c.close()
    broken=ModelClient(Settings(),store,httpx.MockTransport(lambda r:httpx.Response(200,text='data: '+json.dumps({"type":"response.output_text.delta","delta":json.dumps(output)})+'\n\n')))
    with pytest.raises(ModelFailure,match="terminal"):
        await broken.request(context.data)
    assert store.records("token_usage")[0]["payload"]["usage"] is None
    await broken.close()


async def test_model_cancel_records_unknown_usage(store):
    started=asyncio.Event()
    async def handler(request):
        started.set()
        await asyncio.Event().wait()
    client=ModelClient(Settings(),store,httpx.MockTransport(handler))
    task=asyncio.create_task(client.request({}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    record=store.records("token_usage")[0]["payload"]
    assert record["status"]=="cancelled" and record["usage"] is None
    await client.close()


def test_memory_revision_support_and_stable_gate(store,state):
    state.lifecycle='interrupted'
    store.save_event(event("ActionNewRound",{}),state)
    memory=Memories(store)
    m=memory.revise({"claim":"有役保障后再鸣牌","supports":["e1"],"counterexamples":[]},"test",catalog_version="test-v1")
    assert m["status"]=="candidate"
    with pytest.raises(ValueError,match="three"):
        memory.revise({"id":m["id"],"revision":1,"status":"stable","counterexamples":[]},"test")
    with pytest.raises(ValueError,match="forbidden"):
        memory.revise({"claim":"bad","code":"change rules"},"test")
    with pytest.raises(ValueError,match="support"):
        memory.revise({"claim":"bad","supports":["invented"]},"test")
    updated=memory.revise({"id":m["id"],"revision":1,"status":"inactive","reason":"发现反例"},"test")
    assert updated["revision"]==2 and len(memory.history(m["id"]))==2
    assert memory.query()==[]
