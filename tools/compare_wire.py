"""Paired real-channel decisions and factual reading checks; never live input."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from benchmark import sample
from majsoul_llm.config import Settings
from majsoul_llm.domain import Event, Meld, Phase, Snapshot
from majsoul_llm.model import ModelClient
from majsoul_llm.rules import legal_actions
from majsoul_llm.state import apply_event
from majsoul_llm.store import Store
from majsoul_llm.wire import build_context, semantic_context, prompt_for


READ_SYSTEM = """
本次是事实读取验证，不执行打牌。只输出以下JSON字段，不输出q/a/r/p：
self_seat、phase、scores、drawn_index、furiten（完整对象）、
red_tiles（hand中所有赤牌，按原顺序）、menzen（布尔）、
tanyao_blocked_by_fixed_meld（布尔）、current_min（当前最小向听数）、
legal_kinds（actions中kind去重并按英文排序）、
riichi_seats（立直状态非none的绝对座位，升序）、
rivers（四家完整牌河的tile数组，保留顺序）、
first_event_id（public_events第一个event_id，没有则null）、
discard_instances（全部discard及riichi候选的[action_id,hand索引,实际牌名]数组，按actions顺序）。
原样提取事实；紧凑actions/事实引用先按契约理解，不把未知改成false或0。
"""


def fixtures():
    closed = sample(0)
    closed.hand[4] = '5m'
    for seq,kind,data in (
        (2,'ActionDiscardTile',{'seat':0,'tile':'1z'}),
        (3,'ActionDealTile',{'seat':0,'tile':'0m','operation':closed.operation.raw})):
        closed = apply_event(closed,Event(id=f'constructed-red-{seq}',match_id=closed.match_id,seq=seq,
                                          kind=kind,data=data,source='fixture',received_at=closed.observed_at,
                                          received_mono=time.monotonic()))
    yield 'constructed-red-drawn', closed
    threat = sample(1)
    threat.furiten = {'server':True, 'temporary':True, 'riichi':False}
    threat.wind, threat.hand_number = 1, 4
    threat.scores = [42000, 25000, 18000, 15000]
    yield 'constructed-threat-furiten-south4', threat
    opened = sample(2)
    opened.hand = '2m 3m 4m 6m 7m 8m 2p 3p 4p 5s 5s'.split()
    opened.drawn_index = 10
    opened.players[0].melds = [Meld(id='synthetic-pon', actor=0, kind='pon', tiles=['9s']*3,
                                  source_seat=2, called_tile='9s', consumed_tiles=['9s']*2,
                                  event_id='synthetic-pon')]
    yield 'constructed-open-terminal-no-tanyao', opened
    response = sample(2)
    response.hand = '2m 3m 4m 6m 7m 8m 2p 3p 4p 5s 5s 0s 7z'.split()
    response.drawn_index = None
    response.phase = Phase.RESPONSE
    response.context['last_discard'] = {'seat':3, 'tile':'5s', 'event_id':'synthetic-offer'}
    response.operation.raw['operation_list'] = [{'type':3,'combination':['5s|5s','0s|5s']}]
    yield 'constructed-red-multiple-pon-response', response
    riichi = sample(0)
    riichi.operation.raw['operation_list'].append({'type':7,'combination':['1z']})
    yield 'constructed-riichi-plan-memory', riichi
    kan = sample(0)
    kan.hand = '5m 5m 5m 0m 1p 2p 3p 6s 7s 8s 1z 1z 2z 2z'.split()
    kan.operation.raw['operation_list'].append({'type':4,'combination':['5m']})
    yield 'constructed-kan-phase', kan


def cases(store, limit):
    result = list(fixtures())
    count = 0
    for final in store.recent():
        if final.completeness != 'full':
            continue
        current = Snapshot(match_id=final.match_id, self_seat=final.self_seat, rules=final.rules)
        rows, offset = [], 0
        while True:
            page = store.events(final.match_id, offset=offset, limit=200)
            rows.extend(page)
            if len(page) < 200:
                break
            offset += 200
        observed = []
        for row in rows:
            current = apply_event(current, Event.model_validate({**row, 'received_mono':time.monotonic()}))
            if current.operation:
                observed.append(current.model_copy(deep=True))
        for current in ([observed[0],observed[-1]] if len(observed)>1 else observed):
            result.append((f'archive-observed-{count}', current))
            count += 1
            if count >= limit:
                return result
    return result


def expected(state, context):
    return {'self_seat':state.self_seat, 'phase':state.phase.value, 'scores':state.scores,
            'drawn_index':state.drawn_index, 'furiten':state.furiten,
            'red_tiles':[t for t in state.hand if t.startswith('0')],
            **context.data['yaku_constraints'],
            'current_min':context.data['current_shanten']['min'],
            'legal_kinds':sorted({a.kind for a in context.actions.values()}),
            'riichi_seats':[s for s,p in enumerate(state.players) if p.riichi != 'none'],
            'rivers':[[r.tile for r in p.river] for p in state.players],
            'discard_instances':[[key,a.tile_index,state.hand[a.tile_index]] for key,a in context.actions.items()
                                 if a.kind in ('discard','riichi')],
            'first_event_id':context.data['public_events'][0]['event_id'] if state.public_events else None}


async def run(args):
    settings = Settings.load()
    store = Store(settings.runtime/'archive.sqlite')
    client = ModelClient(settings, store)
    report = {'schema':'wire-pairs.v1', 'model':settings.model, 'reasoning':'none',
              'live_input':False, 'all_failures_retained':True, 'rows':[]}
    try:
        for label, state in cases(store, args.archived):
            if args.case and label not in args.case:
                continue
            actions = legal_actions(state)
            plan, memories = None, []
            if label == 'constructed-riichi-plan-memory':
                plan = {'version':1, 'basis_event_ids':[state.public_events[0]['event_id']],
                        'goal':'南场领先争取快和保位', 'approach':'优先门清立直，受威胁时重估',
                        'target_yaku':[{'yaku':'riichi','status':'target','basis':['门清接近听牌'],
                                        'requirements':['门清听牌后才能立直']}],
                        'priorities':['避免第四'], 'switch_conditions':[{'when':'对手立直且价值不足','then':'考虑弃和'}],
                        'revision_reason':'本局初始计划'}
                memories = [{'id':'constructed-memory', 'revision':1, 'status':'candidate',
                             'claim':'低价值手追打前核对点差和威胁', 'conditions':['南场领先'],
                             'supports':['constructed-prior-event']}]
            rich, compact = (build_context(state, actions, compact=c, plan=plan, memories=memories) for c in (False, True))
            assert semantic_context(rich) == semantic_context(compact)
            for repeat in range(args.repeats):
                contexts = (rich, compact) if repeat % 2 == 0 else (compact, rich)
                for context in contexts:
                    context.data['clock']['soft_seconds_remaining'] = 5.0
                    context.data['clock']['decision_seconds_remaining'] = 14.2
                    record = {'case':label, 'source':'constructed' if label.startswith('constructed') else 'archived_observed',
                              'repeat':repeat, 'format':context.data['v'], 'equivalent':True}
                    before_usage = store.db.execute('SELECT COALESCE(MAX(rowid),0) FROM records').fetchone()[0]
                    started = time.monotonic()
                    try:
                        decision, action, metrics = await client.decide(context, time.monotonic()+settings.request_timeout)
                        record.update(decision_valid=True, action={'kind':action.kind,'index':action.tile_index,
                                                                  'consume':action.consume_indices},
                                      reason=decision.reason, metrics=metrics)
                        after = [tile for i,tile in enumerate(state.hand) if i != action.tile_index]
                        contradiction = (action.kind in ('discard','riichi') and state.hand[action.tile_index].startswith('0')
                                         and not any(t.startswith('0') for t in after)
                                         and any(s in decision.reason for s in ('保留赤','保赤','保留红','留赤宝')))
                        record['consistency_flags'] = ['red_discard_claims_keep_red'] if contradiction else []
                    except Exception as error:
                        record.update(decision_valid=False, error_type=type(error).__name__)
                    record['decision_total_ms'] = (time.monotonic()-started)*1000
                    # Include invalid initial attempts and missing usage, not
                    # merely the last successful repair returned by decide().
                    record['decision_attempts'] = [json.loads(r[0]) for r in store.db.execute(
                        "SELECT payload FROM records WHERE rowid>? AND purpose='token_usage' ORDER BY rowid",
                        (before_usage,)) if json.loads(r[0]).get('request_q') == context.q]
                    try:
                        output, metrics = await client.request(context.data, purpose='wire_validation',
                                                               system=prompt_for(context.data['v'])+READ_SYSTEM)
                        truth = expected(state, context)
                        truth.pop('dora_alone_is_not_yaku')
                        wrong = {key:{'expected':value,'actual':output.get(key)} for key,value in truth.items()
                                 if output.get(key) != value}
                        record.update(factual_errors=wrong, reading_metrics=metrics)
                    except Exception as error:
                        record.update(reading_error=type(error).__name__)
                    report['rows'].append(record)
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(json.dumps({key:record.get(key) for key in ('case','repeat','format','decision_valid',
                                                                    'factual_errors','reading_error')}, ensure_ascii=False), flush=True)
    finally:
        await client.close()
        store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--archived', type=int, default=4)
    parser.add_argument('--case', action='append')
    parser.add_argument('--output', type=Path, default=Path('runtime/wire-pairs.json'))
    asyncio.run(run(parser.parse_args()))
