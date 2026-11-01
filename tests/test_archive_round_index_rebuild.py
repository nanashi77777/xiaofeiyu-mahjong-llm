import json
import sqlite3
from pathlib import Path

import pytest

from majsoul_llm.domain import Snapshot
from majsoul_llm.engine import Engine
from majsoul_llm.store import Store
from tools.rebuild_round_indices import apply_plan, plan

from conftest import HAND


async def legacy_archive(path):
    store = Store(path)
    engine = Engine(store)
    engine.state = Snapshot(match_id='fixture',self_seat=0)
    for ju in (0,1):
        await engine.action('ActionNewRound',{'tiles':HAND,'scores':[25000]*4,'chang':0,'ju':ju,'ben':0},0)
    await engine.action('NotifyGameEndResult',{'result':{'players':[
        {'seat':i,'part_point_1':25000,'total_point':0,'rank':i+1} for i in range(4)]}})
    original = json.loads(store.db.execute('SELECT snapshot FROM matches').fetchone()[0])
    assert original['round_seq']==2 and original['lifecycle']=='ended'
    original['round_seq'] = 1
    store.db.execute('UPDATE matches SET snapshot=?',(json.dumps(original),))
    store.db.execute('UPDATE rounds SET seq=1')
    store.db.commit()
    store.record('decision_failure',{'reason':'fixture retained failure'},'fixture')
    store.close()


def contents(path, table):
    with sqlite3.connect(path) as db:
        return db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()


async def test_verified_rebuild_is_backed_up_atomic_and_preserves_all_original_evidence(tmp_path):
    path = tmp_path/'archive.sqlite'
    await legacy_archive(path)
    before = {t:contents(path,t) for t in ('events','matches','records')}
    proposal = plan(path)
    assert proposal['production_data_modified'] is False and len(proposal['changes'])==1
    assert proposal['changes'][0]['snapshot_index']=={'before':1,'after':2}
    assert contents(path,'matches')==before['matches']
    result = apply_plan(path,proposal)
    assert result['changed_matches']==1 and result['changed_rounds']==1
    backup = Path(result['backup'])
    for table,rows in before.items():
        assert contents(backup,table)==rows
    assert contents(path,'events')==before['events']
    after_match = contents(path,'matches')[0]
    assert after_match[:4]==before['matches'][0][:4]
    old,new = json.loads(before['matches'][0][4]),json.loads(after_match[4])
    assert new.pop('round_seq')==2 and old.pop('round_seq')==1 and old==new
    assert contents(path,'records')[:len(before['records'])]==before['records']
    assert len(contents(path,'records'))==len(before['records'])+1
    assert not plan(path)['changes']
    assert apply_plan(path,plan(path))['changed_matches']==0


@pytest.mark.parametrize('damage',['active','interrupted','partial','row_partial','scores','gap'])
async def test_ineligible_or_non_index_damage_never_proposes_repair(tmp_path,damage):
    path = tmp_path/'archive.sqlite'
    await legacy_archive(path)
    with sqlite3.connect(path) as db:
        raw = json.loads(db.execute('SELECT snapshot FROM matches').fetchone()[0])
        if damage in ('active','interrupted'):
            raw['lifecycle']=damage
            db.execute('UPDATE matches SET lifecycle=?',(damage,))
        elif damage=='partial':
            raw['completeness']='partial'
        elif damage=='row_partial':
            db.execute("UPDATE matches SET completeness='partial'")
        elif damage=='scores':
            raw['scores']=[24000,26000,25000,25000]
        else:
            db.execute('UPDATE events SET seq=seq+1 WHERE seq=2')
        db.execute('UPDATE matches SET snapshot=?',(json.dumps(raw),))
    proposal = plan(path)
    assert not proposal['changes']
    assert proposal['skipped'][0]['reason']!='indices_already_match'


async def test_stale_and_tampered_plan_cannot_mutate_archive(tmp_path):
    path = tmp_path/'archive.sqlite'
    await legacy_archive(path)
    proposal = plan(path)
    before = contents(path,'matches')
    proposal['changes'][0]['snapshot_index']['after'] = 8
    with pytest.raises(ValueError,match='stale_or_invalid'):
        apply_plan(path,proposal)
    assert contents(path,'matches')==before
    assert len(contents(path,'records'))==1
    proposal = plan(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE matches SET lifecycle='active'")
    before = contents(path,'matches')
    with pytest.raises(ValueError,match='stale_before_backup'):
        apply_plan(path,proposal)
    assert contents(path,'matches')==before and len(contents(path,'records'))==1


async def test_provenance_failure_rolls_back_all_index_updates(tmp_path):
    path = tmp_path/'archive.sqlite'
    await legacy_archive(path)
    proposal = plan(path)
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TRIGGER reject_repair BEFORE INSERT ON records
            WHEN NEW.purpose='archive_repair' BEGIN SELECT RAISE(ABORT,'fixture'); END""")
    before = {t:contents(path,t) for t in ('matches','rounds','events','records')}
    with pytest.raises(sqlite3.IntegrityError):
        apply_plan(path,proposal)
    assert all(contents(path,t)==rows for t,rows in before.items())
