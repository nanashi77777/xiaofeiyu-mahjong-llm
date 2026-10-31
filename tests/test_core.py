
import pytest

from conftest import HAND, event
from majsoul_llm.domain import Meld, Phase, Snapshot, tile_index
from majsoul_llm.engine import Engine
from majsoul_llm.rules import legal_actions, score, shanten, visible_counts
from majsoul_llm.state import apply_event
from majsoul_llm.timing import TimeBank
from majsoul_llm.wire import build_context, semantic_actions, semantic_context
from majsoul_llm.yaku import YakuReference


def test_red_and_immutable_actual_state(state):
    original = state.model_copy(deep=True)
    state.hand[-1] = "0m"
    state.drawn_index = 13
    result = apply_event(state, event("ActionDiscardTile", {"seat": 0, "tile": "0m", "moqie": True}, 2))
    assert result.hand == state.hand[:-1] and state.hand[-1] == "0m"
    assert result.players[0].river[-1].tile == "0m"
    assert original.players[0].river == []
    with pytest.raises(ValueError, match="tsumogiri"):
        apply_event(state, event("ActionDiscardTile", {"seat": 0, "tile": "5m", "moqie": True}, 3))
    assert tile_index("0m") == tile_index("5m")


def test_initial_dealer_hand_has_no_identified_draw_until_deal_event(state):
    assert len(state.hand) == 14 and state.drawn_index is None
    state = apply_event(state,event('ActionDiscardTile',{'seat':0,'tile':'1z'},2))
    state = apply_event(state,event('ActionDealTile',{'seat':0,'tile':'0p'},3))
    assert state.drawn_index == 13 and state.hand[state.drawn_index] == '0p'


def test_meld_consumption_and_called_discard_count():
    s = Snapshot(match_id="test", self_seat=0, hand="1m 2m 5p 5p 1p 2p 3p 6s 7s 8s 1z 1z 2z".split())
    s = apply_event(s, event("ActionDiscardTile", {"seat": 3, "tile": "3m"}))
    before = visible_counts(s)
    s = apply_event(s, event("ActionChiPengGang", {"seat": 0, "type": 0, "tiles": ["1m", "2m", "3m"], "froms": [0, 0, 3]}, 2))
    assert s.phase == Phase.DISCARD and len(s.hand) == 11
    assert s.players[3].river[0].called_by == 0
    assert visible_counts(s) == before
    s = apply_event(s, event("ActionDiscardTile", {"seat": 0, "tile": "2z"}, 3))
    assert len(s.hand) + 3 == 13


def test_kan_and_riichi_payments():
    s = Snapshot(match_id="test", self_seat=0, hand="5m 5m 5m 0m 1p 2p 3p 6s 7s 8s 1z 1z 2z 2z".split(), scores=[25000]*4)
    s = apply_event(s, event("ActionAnGangAddGang", {"seat": 0, "type": 3, "tiles": "5m"}))
    assert s.phase == Phase.REPLACEMENT and len(s.hand) == 10
    assert "0m" in s.players[0].melds[0].tiles
    s = apply_event(s, event("ActionDealTile", {"seat": 0, "tile": "9p", "liqi": {"seat": 2, "score": 24000, "liqibang": 1}}, 2))
    assert len(s.hand) + 3 == 14 and s.context["rinshan"]
    assert s.players[2].riichi == "established" and s.scores[2] == 24000


def test_shanten_scoring_and_all_instances(state):
    assert shanten(HAND)["min"] == -1
    assert shanten(HAND[:-1])["min"] == 0
    assert shanten("1m 1m 2m 2m 3p 3p 4p 4p 5s 5s 6s 6s 7z".split())["seven_pairs"] == 0
    assert shanten("1m 9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z".split())["kokushi"] == 0
    actions = legal_actions(state)
    assert len(actions) == 14 and {a.tile_index for a in actions} == set(range(14))
    value = score(state, HAND[:-1], "1z", tsumo=True)
    assert value["legal_yaku"] and value["han"] >= 1
    # Dora alone never rescues a no-yaku open complete hand.
    s = state.model_copy(deep=True)
    s.players[0].melds = [Meld(id="m", actor=0, kind="chi", tiles=["1m","2m","3m"], event_id="m")]
    value = score(s, "4m 5m 6m 2p 3p 4p 6s 7s 8s 2z".split(), "2z")
    assert value["error"] == "no_yaku"


def test_kuikae_red_and_riichi_forced_slot(state):
    state.hand[4] = "0m"
    state.operation.raw["operation_list"][0]["combination"] = ["5m"]
    assert all(a.tile_index != 4 for a in legal_actions(state))
    state.players[0].riichi = "established"
    state.drawn_index = 13
    assert [a.tile_index for a in legal_actions(state)] == [13]


def test_wire_information_equivalence_and_evidence(state):
    state.unknown_fields = ["test_unknown"]
    actions = legal_actions(state)
    rich = build_context(state, actions)
    compact = build_context(state, actions, compact=True)
    assert semantic_actions(rich) == semantic_actions(compact)
    assert semantic_context(rich) == semantic_context(compact)
    assert compact.data["unknown_fields"] == ["test_unknown"]
    assert compact.data["current_shanten"]["min"] == -1
    assert "catalog_version" not in rich.data and "yaku_reference" not in rich.data
    rich.validate({"q":rich.q, "action_id":"a0", "reason":"合法操作", "plan_update":None})
    with pytest.raises(ValueError):
        rich.validate({"q":"wrong", "action_id":"a0", "reason":"错", "plan_update":None})
    schema = compact.response_schema()
    assert schema['properties']['q']['const'] == compact.q
    assert schema['properties']['a']['enum'] == list(compact.actions)
    assert schema['$defs']['RoundPlanUpdate']['properties']['v']['const'] == 0
    assert schema['$defs']['RoundPlanUpdate']['properties']['e']['items']['enum'] == [
        e['event_id'] for e in compact.data['public_events']]


def test_time_bank_round_scoped_and_no_reconnect_refill(state):
    bank = TimeBank(bank=10, margin=.5)
    bank.enter_round("r1")
    assert bank.charge("w1", 7) == 8
    assert bank.charge("w1", 100) == 8
    bank.enter_round("r1")
    assert bank.remaining == 8
    start = state.operation.received_mono
    assert bank.deadline(state.operation, now=start, verified=False) == start+4.5
    state.operation.historical = True
    with pytest.raises(ValueError):
        bank.deadline(state.operation, now=start)
    bank.enter_round("r2")
    assert bank.remaining == 10


def test_verified_server_bank_clamps_and_deal_animation_does_not_spend_pool(state):
    bank = TimeBank(base=5,bank=20,margin=.8)
    bank.enter_round('actual-round')
    op = state.operation
    op.fixed_ms,op.add_ms = 8000,10000
    assert bank.deadline(op,now=op.received_mono,verified=True) == op.received_mono+17.2
    assert bank.remaining == 10
    assert bank.charge(op.window_id,7,fixed_ms=8000) == 10
    op = op.model_copy(update={'window_id':'next','fixed_ms':5000,'add_ms':0})
    assert bank.deadline(op,now=op.received_mono,verified=True) == op.received_mono+4.2
    assert bank.remaining == 0
    bank.enter_round('actual-round')
    assert bank.remaining == 0


async def test_event_idempotency_recovery_multiwin_and_end(store):
    engine = Engine(store)
    engine.state = Snapshot(match_id="test", self_seat=0)
    new = {"tiles":HAND, "scores":[25000]*4, "md5":"synthetic-round-id"}
    await engine.action("ActionNewRound", new, 0)
    round_uid = engine.state.round_uid
    await engine.action("ActionDiscardTile", {"seat":0,"tile":"1z"}, 1)
    count = len(engine.state.hand)
    await engine.action("ActionDiscardTile", {"seat":0,"tile":"1z"}, 1)
    assert len(engine.state.hand) == count
    await engine.restore({"actions":[{"name":"ActionNewRound","step":0,"data":new},
                                    {"name":"ActionDiscardTile","step":1,"data":{"seat":0,"tile":"1z"}}]})
    assert engine.state.round_uid == round_uid and len(engine.state.hand) == count
    assert engine.state.operation is None and len(store.events("test")) == 2
    await engine.action("ActionHule", {"scores":[10000,40000,30000,20000], "hules":[{"seat":1,"count":6,"fu":30},{"seat":2,"count":8,"fu":30}]}, 2)
    assert store.db.execute("SELECT COUNT(*) FROM wins").fetchone()[0] == 2
    assert engine.state.lifecycle == "active" and engine.state.phase == Phase.ROUND_END
    players = [{"seat":s,"part_point_1":p} for s,p in [(1,40000),(2,30000),(3,20000),(0,10000)]]
    await engine.action("NotifyGameEndResult", {"result":{"players":players}})
    assert engine.state.final_ranks == [4,1,2,3] and engine.state.lifecycle == "ended"


def test_yaku_reference_selected_only():
    yaku = YakuReference()
    selected = yaku.read(ids=["riichi"])
    assert len(selected["entries"]) < len(yaku.entries)
    assert len(yaku.read(all_entries=True)["entries"]) == 50
    with pytest.raises(ValueError):
        yaku.read(ids=["invented"])


async def test_new_complete_round_does_not_hide_missing_match_history(store):
    engine = Engine(store)
    engine.state = Snapshot(match_id='joined-midmatch',self_seat=0)
    await engine.action('ActionNewRound',{'chang':1,'ju':0,'tiles':HAND,'scores':[25000]*4},0)
    assert engine.state.round_completeness == 'full' and engine.state.completeness == 'partial'
    await engine.action('ActionHule',{'hules':[]},1)
    await engine.action('ActionNewRound',{'chang':1,'ju':0,'ben':1,'tiles':HAND,'scores':[25000]*4},0)
    assert engine.state.round_completeness == 'full' and engine.state.completeness == 'partial'
    await engine.gap('injected')
    await engine.action('ActionNewRound',{'chang':1,'ju':0,'ben':2,'tiles':HAND,'scores':[25000]*4},0)
    assert engine.state.consistent and engine.state.round_completeness == 'full'
    assert store.latest(engine.state.match_id).completeness == 'gaps'


@pytest.mark.parametrize('kind,data',[
    ('ActionNoTile',{'scores':[{'old_scores':[25000]*4,'delta_scores':[3000,-1000,-1000,-1000]}]}),
    *[('ActionLiuJu',{'type':reason}) for reason in range(1,6)],
])
def test_standard_round_result_and_west_extension_are_not_match_end(state,kind,data):
    ended=apply_event(state,event(kind,data,2))
    assert ended.phase == Phase.ROUND_END and ended.lifecycle == 'active'
    assert ended.final_scores is None and ended.final_ranks is None
    west=apply_event(ended,event('ActionNewRound',{'chang':2,'ju':0,'ben':1,'tiles':HAND,'scores':[28000,24000,24000,24000]},3))
    assert west.wind == 2 and west.honba == 1 and west.round_seq == 2
    assert west.round_uid != ended.round_uid and west.dealer == 0 and west.lifecycle == 'active'
