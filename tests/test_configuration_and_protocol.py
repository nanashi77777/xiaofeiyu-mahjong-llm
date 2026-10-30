import json
import struct

import pytest
from google.protobuf import descriptor_pb2

from majsoul_llm.config import Settings
from majsoul_llm.domain import Discard, Meld, Snapshot
from majsoul_llm.protocol import Protocol, xor_action
from majsoul_llm.store import Store


def test_private_settings_round_trip_and_no_dump_secret(tmp_path):
    from pydantic import SecretStr
    path=tmp_path/'private.json'
    settings=Settings(runtime=tmp_path,api_key=SecretStr('test-credential-not-real'))
    settings.save_private(path)
    assert 'test-credential-not-real' not in settings.model_dump_json()
    restored=Settings.load(path)
    assert restored.api_key.get_secret_value()=='test-credential-not-real'
    assert restored.read_token!=restored.control_token
    assert restored.input_mode=='physical_foreground' and not restored.timer_verified
    assert not restored.compact_validated


def test_domain_rejects_invalid_seats_and_tiles():
    for seat in (-1,4):
        with pytest.raises(ValueError):
            Snapshot(match_id='bad',self_seat=seat)
    for tile in ('0z','8z','10m','xx'):
        with pytest.raises(ValueError):
            Discard(tile=tile,event_id='x',seq=1)
        with pytest.raises(ValueError):
            Meld(id='x',actor=0,kind='chi',tiles=[tile],event_id='x')


def fixture_protocol():
    # Independently authored minimal protobuf fixture; no third-party schema needed.
    file=descriptor_pb2.FileDescriptorProto(name='test.proto',package='lq',syntax='proto3')
    def message(name,fields):
        m=file.message_type.add(name=name)
        for number,(key,kind) in enumerate(fields,1):
            m.field.add(name=key,number=number,type=kind,label=1)
    message('Wrapper',[('name',9),('data',12)])
    message('ReqAuth',[('account_id',13),('game_uuid',9),('token',9)])
    message('ResAuth',[('is_game_start',8)])
    message('ActionDiscardTile',[('seat',13),('tile',9)])
    message('ActionPrototype',[('step',13),('name',9),('data',12)])
    service=file.service.add(name='FastTest')
    service.method.add(name='authGame',input_type='.lq.ReqAuth',output_type='.lq.ResAuth')
    return Protocol(file.SerializeToString())


def test_wire_protocol_sanitizes_auth_and_live_vs_recovery():
    protocol=fixture_protocol()
    wrapper=protocol.message('lq.Wrapper')
    request=protocol.message('lq.ReqAuth')(account_id=123,game_uuid='fixture-match',token='must-not-leak')
    frame=b'\x02'+struct.pack('<H',3)+wrapper(name='.lq.FastTest.authGame',data=request.SerializeToString()).SerializeToString()
    result=protocol.frame('c1',frame,True)
    assert result['data']=={'account_id':123,'game_uuid':'fixture-match'}
    assert 'must-not-leak' not in json.dumps(result)
    response=b'\x03'+struct.pack('<H',3)+wrapper(data=protocol.message('lq.ResAuth')(is_game_start=True).SerializeToString()).SerializeToString()
    assert protocol.frame('c1',response,False)['data']['is_game_start']
    assert protocol.frame('c1',response,False) is None
    action=protocol.message('lq.ActionDiscardTile')(seat=2,tile='0p').SerializeToString()
    assert protocol.action('ActionDiscardTile',xor_action(action))==protocol.action('ActionDiscardTile',action,historical=True)
    assert xor_action(xor_action(action))==action
    prototype=protocol.message('lq.ActionPrototype')(step=17,name='ActionDiscardTile',data=xor_action(action))
    notify=b'\x01'+wrapper(name='.lq.ActionPrototype',data=prototype.SerializeToString()).SerializeToString()
    assert protocol.frame('c1',notify,False)['data']['data']=={'seat':2,'tile':'0p'}


def test_store_committed_state_survives_reopen_and_future_schema_rejected(tmp_path,state):
    from conftest import event
    path=tmp_path/'archive.sqlite'
    store=Store(path)
    store.save_event(event('ActionNewRound',{}),state)
    store.close()
    store=Store(path)
    assert store.latest().hand==state.hand and len(store.events('test'))==1
    store.mark_interrupted()
    assert store.latest().lifecycle=='interrupted' and store.latest().operation is None
    store.db.execute('PRAGMA user_version=99')
    store.close()
    with pytest.raises(ValueError,match='newer'):
        Store(path)
