"""Deterministic event reducer, with no dependency on model output."""
from __future__ import annotations

import hashlib
from collections import Counter

from .domain import Discard, Event, Meld, Operation, Phase, Player, Snapshot, tile_index


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:20]


def _remove(hand, tile, *, last=False):
    indices = [i for i, value in enumerate(hand) if value == tile]
    if not indices:
        raise ValueError("actual event consumes absent tile")
    hand.pop(indices[-1] if last else indices[0])


def apply_event(previous: Snapshot, event: Event) -> Snapshot:
    if previous.match_id != event.match_id:
        raise ValueError("match mismatch")
    state = previous.model_copy(deep=True)
    d, kind = event.data, event.kind
    state.state_version += 1
    state.last_event_id = event.id
    state.observed_at = event.received_at
    state.operation = None
    state.lifecycle = "active"
    if kind == "ActionMJStart":
        state.phase = Phase.WAITING
    elif kind == 'ObservedPass':
        # This event is emitted only after the passive adapter observes the
        # server accepting cancel_operation, never from a model proposal.
        if d['declined_ron']:
            state.furiten['riichi' if state.players[state.self_seat].riichi == 'established' else 'temporary'] = True
        state.phase = Phase.WAITING
    elif kind == "ActionNewRound":
        state.round_seq += 1
        state.round_uid = state.match_id + ":" + digest(event.id)
        state.wind, state.hand_number, state.honba = d.get("chang", 0), d.get("ju", 0) + 1, d.get("ben", 0)
        state.dealer = d.get("ju", 0)
        state.deposits = d.get("liqibang", 0)
        state.players = [Player() for _ in range(4)]
        for seat,player in enumerate(state.players):
            player.concealed_count=14 if seat==state.dealer else 13
        first_observed_round = previous.round_seq == 0 and previous.round_uid is None
        if first_observed_round:
            state.completeness = ('full' if state.wind == 0 and state.hand_number == 1
                                  and state.honba == 0 and state.deposits == 0 else 'partial')
        state.round_completeness = "full"
        state.consistent = True
        state.unknown_fields = []
        state.hand = list(d.get("tiles", []))
        # The initial dealer receives 14 unsorted tiles. Unity sorts all of
        # them and displays the last sorted tile across a gap; the last item
        # in ActionNewRound.tiles is not an identified draw instance.
        state.drawn_index = None
        state.dora = list(d.get("doras") or ([d["dora"]] if d.get("dora") else []))
        state.public_events = []
        state.context = {"first_turn": True, "last_discard": None, "new_round_signature": digest(event.id),
                         "draw_counts": [0] * 4, "ippatsu": [False] * 4, "calls_seen": False}
        state.furiten = {"server": None, "temporary": False, "riichi": False}
        state.phase = Phase.DISCARD if len(state.hand) % 3 == 2 else Phase.WAITING
    elif kind == "ActionDealTile":
        actor = d.get("seat", 0)
        state.turn = actor
        if state.players[actor].concealed_count is not None:
            state.players[actor].concealed_count+=1
        state.context.setdefault("draw_counts", [0] * 4)[actor] += 1
        last_kan = state.context.get("last_kan")
        state.context["rinshan"] = bool(last_kan and last_kan["seat"] == actor)
        state.context["last_kan"] = None
        if actor == state.self_seat:
            if not d.get("tile"):
                raise ValueError("own draw has no visible tile")
            state.hand.append(d["tile"])
            state.drawn_index = len(state.hand) - 1
            state.phase = Phase.DISCARD
            state.furiten["temporary"] = False
        else:
            state.phase = Phase.WAITING
    elif kind == "ActionDiscardTile":
        actor, tile = d.get("seat", 0), d["tile"]
        if state.players[actor].concealed_count is not None:
            state.players[actor].concealed_count-=1
        if actor == state.self_seat:
            if d.get("moqie") and state.drawn_index is not None:
                if state.hand[state.drawn_index] != tile:
                    raise ValueError("tsumogiri tile mismatch")
                state.hand.pop(state.drawn_index)
            else:
                _remove(state.hand, tile)
            state.hand.sort(key=lambda t: (tile_index(t), t[0] == "0"))
            state.drawn_index = None
        riichi = bool(d.get("is_liqi") or d.get("is_wliqi"))
        if state.players[actor].riichi == "established":
            state.context.setdefault("ippatsu", [False] * 4)[actor] = False
        state.players[actor].river.append(Discard(tile=tile, event_id=event.id, seq=event.seq,
                                                tsumogiri=d.get("moqie", False), riichi=riichi))
        if riichi:
            state.players[actor].riichi = "declared"
            state.players[actor].riichi_event = event.id
            state.context.setdefault("ippatsu", [False] * 4)[actor] = True
            state.context.setdefault("double_riichi", [False] * 4)[actor] = bool(d.get("is_wliqi"))
        state.context["last_discard"] = {"tile": tile, "seat": actor, "event_id": event.id}
        state.phase = Phase.RESPONSE if d.get("operation") else Phase.WAITING
    elif kind == "ActionChiPengGang":
        actor = d.get("seat", 0)
        tiles, origins = list(d.get("tiles", [])), list(d.get("froms", []))
        if len(tiles) != len(origins):
            raise ValueError("meld sources missing")
        foreign = [(t, source) for t, source in zip(tiles, origins, strict=True) if source != actor]
        if len(foreign) != 1:
            raise ValueError("called meld needs one foreign tile")
        consumed = [t for t, source in zip(tiles, origins, strict=True) if source == actor]
        if state.players[actor].concealed_count is not None:
            state.players[actor].concealed_count-=len(consumed)
        called, source = foreign[0]
        meld_kind = {0: "chi", 1: "pon", 2: "daiminkan"}[d.get("type", 0)]
        if actor == state.self_seat:
            for tile in consumed:
                _remove(state.hand, tile)
            state.drawn_index = None
        discard = next((t for t in reversed(state.players[source].river) if t.tile == called and t.called_by is None), None)
        if discard is None:
            raise ValueError("called discard not observed")
        discard.called_by = actor
        state.players[actor].melds.append(Meld(id=event.id, actor=actor, kind=meld_kind, tiles=tiles,
                                              source_seat=source, called_tile=called, consumed_tiles=consumed,
                                              source_event=discard.event_id, event_id=event.id))
        state.phase = (Phase.REPLACEMENT if meld_kind == "daiminkan" else Phase.DISCARD) if actor == state.self_seat else Phase.WAITING
        state.turn = actor
        state.context.update(first_turn=False, calls_seen=True, ippatsu=[False] * 4)
        if meld_kind == "daiminkan":
            state.context["last_kan"] = {"seat": actor, "tile": called, "type": "daiminkan"}
    elif kind == "ActionAnGangAddGang":
        actor, tile = d.get("seat", 0), d["tiles"]
        def same(t):
            return tile_index(t) == tile_index(tile)
        if d.get("type") == 2:
            meld = next((m for m in state.players[actor].melds if m.kind == "pon" and same(m.tiles[0])), None)
            if meld is None:
                raise ValueError("added kan has no pon")
            if actor == state.self_seat:
                _remove(state.hand, tile)
            if state.players[actor].concealed_count is not None:
                state.players[actor].concealed_count-=1
            meld.upgraded_from = meld.id
            meld.kind, meld.event_id = "kakan", event.id
            meld.tiles.append(tile)
            meld.consumed_tiles.append(tile)
        elif d.get("type") == 3:
            if state.players[actor].concealed_count is not None:
                state.players[actor].concealed_count-=4
            # Opponent concealed-kan messages give a tile TYPE, not four
            # physical color identities. Never expand 0p into four red fives.
            red_unknown = actor != state.self_seat and tile_index(tile) in (4,13,22) and state.rules.aka_dora
            base_tile = '5'+tile[1] if tile.startswith('0') else tile
            group = [t for t in state.hand if same(t)] if actor == state.self_seat else [base_tile] * 4
            if len(group) != 4:
                raise ValueError("concealed kan requires four tiles")
            if actor == state.self_seat:
                for t in group:
                    _remove(state.hand, t)
            state.players[actor].melds.append(Meld(id=event.id, actor=actor, kind="ankan", tiles=group,
                                                  consumed_tiles=group if actor == state.self_seat else [],
                                                  red_count_unknown=red_unknown,event_id=event.id))
        else:
            raise ValueError("unsupported kan event type")
        state.drawn_index = None if actor == state.self_seat else state.drawn_index
        state.phase = Phase.RESPONSE if d.get("operation") else Phase.REPLACEMENT if actor == state.self_seat else Phase.WAITING
        state.context.update(first_turn=False, calls_seen=True, ippatsu=[False] * 4, last_kan={"seat": actor, "tile": tile, "type": d["type"]})
    elif kind in ("ActionHule", "ActionNoTile", "ActionLiuJu"):
        state.phase = Phase.ROUND_END
        if kind == "ActionNoTile" and d.get("scores"):
            scored = [r for r in d["scores"] if len(r.get("old_scores", [])) == 4]
            if scored:
                initial = scored[0]["old_scores"]
                deltas = [0] * 4
                for result in scored:
                    for seat, delta in enumerate(result.get("delta_scores", [])):
                        deltas[seat] += delta
                state.scores = [initial[s] + deltas[s] for s in range(4)]
        state.context["round_result"] = {"kind": kind, "event_id": event.id}
    elif kind == "NotifyGameEndResult":
        players = d.get("result", {}).get("players", [])
        if len(players) != 4 or {p.get("seat", 0) for p in players} != set(range(4)):
            raise ValueError("incomplete final result")
        state.final_scores = [0] * 4
        state.final_ranks = [0] * 4
        for rank, player in enumerate(players, 1):
            seat = player.get("seat", 0)
            state.final_scores[seat] = player["part_point_1"]
            state.final_ranks[seat] = rank
        state.scores = state.final_scores[:]
        state.lifecycle, state.phase = "ended", Phase.ENDED
    elif kind == "NotifyGameTerminate":
        state.lifecycle, state.phase = "interrupted", Phase.INTERRUPTED
    else:
        raise ValueError("unsupported standard action: " + kind)

    if d.get("doras"):
        state.dora = list(d["doras"])
    if "left_tile_count" in d:
        state.remaining = d["left_tile_count"]
    if d.get("scores") and all(isinstance(n, int) for n in d["scores"]):
        state.scores = list(d["scores"])
    if "zhenting" in d:
        state.furiten["server"] = d["zhenting"]
    liqi = d.get("liqi")
    if liqi and not liqi.get("failed", False):
        seat = liqi.get("seat", 0)
        state.players[seat].riichi = "established"
        if state.scores is not None:
            state.scores[seat] = liqi["score"]
        state.deposits = liqi.get("liqibang", state.deposits)
    raw_operation = d.get("operation")
    if raw_operation and raw_operation.get("operation_list"):
        if raw_operation.get("seat", state.self_seat) != state.self_seat:
            raise ValueError("operation belongs to another seat")
        state.operation = Operation(window_id=event.id + ":op", state_version=state.state_version,
                                    raw=raw_operation, received_mono=event.received_mono, received_at=event.received_at,
                                    fixed_ms=raw_operation.get("time_fixed"), add_ms=raw_operation.get("time_add"), historical=event.historical)
    state.public_events.append(public_event(event))
    validate_state(state)
    return state


def public_event(event: Event) -> dict:
    """Deterministic view shared by the reducer and the archive cache reader."""
    d, kind = event.data, event.kind
    public = {"event_id": event.id, "seq": event.seq, "kind": kind}
    for key in ("seat", "tile", "type", "is_liqi", "is_wliqi", "moqie", "doras"):
        if key in d and not (kind == "ActionDealTile" and key == "tile"):
            public[key] = d[key]
    if kind in ("ActionChiPengGang", "ActionAnGangAddGang"):
        public.update({k: d[k] for k in ("tiles", "froms") if k in d})
    if kind == 'ObservedPass':
        public['declined_ron'] = d['declined_ron']
    if d.get("liqi"):
        public["liqi"] = d["liqi"]
    return public


def validate_state(state: Snapshot):
    if state.phase in (Phase.ROUND_END, Phase.ENDED, Phase.INTERRUPTED) or not state.hand:
        return
    melds = state.players[state.self_seat].melds
    effective = len(state.hand) + 3 * len(melds)
    if effective not in (13, 14):
        raise ValueError(f"invalid effective own tile count: {effective}")
    own = Counter(tile_index(t) for t in state.hand)
    own.update(tile_index(t) for m in melds for t in m.tiles)
    if any(n > 4 for n in own.values()):
        raise ValueError("too many copies of tile")
