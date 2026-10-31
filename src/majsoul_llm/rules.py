"""All server-constrained choices and deterministic facts, never strategy ranking."""
from __future__ import annotations

from collections import Counter
from itertools import product

from mahjong.hand_calculating.hand import HandCalculator
from mahjong.hand_calculating.hand_config import HandConfig, OptionalRules
from mahjong.hand_calculating.scores import ScoresCalculator
from mahjong.meld import Meld as CalcMeld
from mahjong.shanten import Shanten

from .domain import LegalAction, Meld, Snapshot, tile_index, tile_name

KINDS = {1: "discard", 2: "chi", 3: "pon", 4: "ankan", 5: "daiminkan", 6: "kakan", 7: "riichi",
         8: "tsumo", 9: "ron", 10: "abort9"}


def counts(tiles):
    result = [0] * 34
    for tile in tiles:
        result[tile_index(tile)] += 1
    return result


def shanten(tiles, meld_count=0):
    values = counts(tiles)
    calc = Shanten()
    standard = calc.calculate_shanten_for_regular_hand(values)
    seven = calc.calculate_shanten_for_chiitoitsu_hand(values) if not meld_count else None
    kokushi = calc.calculate_shanten_for_kokushi_hand(values) if not meld_count else None
    return {"regular": standard, "seven_pairs": seven, "kokushi": kokushi,
            "min": min(n for n in (standard, seven, kokushi) if n is not None)}


def visible_counts(state):
    visible = Counter(tile_index(t) for t in state.hand + state.dora)
    for player in state.players:
        # Called discards also appear in melds: count each physical tile once.
        visible.update(tile_index(r.tile) for r in player.river if r.called_by is None)
        visible.update(tile_index(t) for m in player.melds for t in m.tiles)
    return visible


def routes(hand, melds, state):
    """Explain verifiable prerequisites, without scoring or ranking routes."""
    all_tiles = hand + [t for meld in melds for t in meld.tiles]
    c = Counter(tile_index(t) for t in all_tiles)
    closed = all(m.closed for m in melds)
    result = []
    if closed:
        result.append({"id": "riichi", "name": "立直", "status": "conditional", "requires": "门清听牌且满足立直条件"})
    honor_types = [(31, "yakuhai_haku", "白"), (32, "yakuhai_hatsu", "发"), (33, "yakuhai_chun", "中"),
                   (27 + (state.self_seat - state.dealer) % 4, "yakuhai_seat_wind", "自风"),
                   (27 + state.wind, "yakuhai_round_wind", "场风")]
    for index, id, name in honor_types:
        fixed = any(tile_index(m.tiles[0]) == index and len(set(map(tile_index, m.tiles))) == 1 for m in melds)
        if fixed or c[index] >= 2:
            result.append({"id": id, "name": name, "status": "fixed_meld" if fixed else "conditional",
                           "count": c[index], "requires": "完成有效和牌形；对子还需成刻/杠" if not fixed else "完成有效和牌形"})
    outside = sum(n for k, n in c.items() if k >= 27 or k % 9 in (0, 8))
    fixed_outside = any(tile_index(t) >= 27 or tile_index(t) % 9 in (0, 8) for m in melds for t in m.tiles)
    result.append({"id": "tanyao", "name": "断幺九", "outside_count": outside,
                   "possible_with_fixed_melds": not fixed_outside and (closed or state.rules.open_tanyao),
                   "requires": "最终全部为2至8数牌；副露需允许食断"})
    for suit, label in enumerate(("万", "筒", "索")):
        others = sum(n for k, n in c.items() if k < 27 and k // 9 != suit)
        if others <= 3:
            result.append({"id": "honitsu", "name": "混一色", "suit": label, "other_suit_count": others,
                           "honor_count": sum(n for k, n in c.items() if k >= 27), "status": "conditional",
                           "requires": "最终同一数牌花色与字牌，门清3番/副露2番"})
    return result


def score(state, hand, win_tile, melds=None, *, tsumo=False, riichi=False, actual=False):
    melds = melds if melds is not None else state.players[state.self_seat].melds
    used = set()

    def allocate(tile):
        index = tile_index(tile)
        choices = list(range(index * 4, index * 4 + 4))
        if index in (4, 13, 22) and state.rules.aka_dora:
            choices = choices[:1] if tile.startswith("0") else choices[1:]
        result = next((n for n in choices if n not in used), None)
        if result is None:
            raise ValueError("physical tile allocation inconsistent")
        used.add(result)
        return result

    try:
        ids = [allocate(t) for t in hand]
        win_id = allocate(win_tile)
        ids.append(win_id)
        called = []
        for meld in melds:
            group = [allocate(t) for t in meld.tiles]
            ids.extend(group)
            kind = CalcMeld.CHI if meld.kind == "chi" else CalcMeld.PON if meld.kind == "pon" else CalcMeld.KAN
            called.append(CalcMeld(kind, group, opened=not meld.closed))
        indicators = [allocate(t) for t in state.dora]
        options = OptionalRules(has_open_tanyao=state.rules.open_tanyao, has_aka_dora=state.rules.aka_dora,
                                has_double_yakuman=state.rules.double_yakuman, kiriage=state.rules.kiriage,
                                kazoe_limit=0 if state.rules.counted_yakuman else 1)
        established = state.players[state.self_seat].riichi == "established"
        double = established and state.context.get("double_riichi", [False] * 4)[state.self_seat]
        last_kan = state.context.get("last_kan")
        first = actual and tsumo and not state.context.get('calls_seen',True) and not state.players[state.self_seat].river
        initial_dealer = first and state.self_seat == state.dealer and not any(p.river for p in state.players)
        first_non_dealer_draw = first and state.self_seat != state.dealer and state.context.get('draw_counts',[0]*4)[state.self_seat] == 1
        config = HandConfig(is_tsumo=tsumo, is_riichi=(riichi or established) and not double,
                            is_daburu_riichi=double,
                            is_ippatsu=actual and established and state.context.get("ippatsu", [False] * 4)[state.self_seat],
                            is_rinshan=actual and tsumo and state.context.get("rinshan", False),
                            is_chankan=actual and not tsumo and bool(last_kan and last_kan.get("type") == 2),
                            is_haitei=actual and tsumo and state.remaining == 0 and not state.context.get("rinshan"),
                            is_houtei=actual and not tsumo and state.remaining == 0 and not last_kan,
                            is_tenhou=initial_dealer,is_chiihou=first_non_dealer_draw,
                            player_wind=27 + (state.self_seat - state.dealer) % 4, round_wind=27 + state.wind,
                            tsumi_number=state.honba, kyoutaku_number=state.deposits, options=options)
        result = HandCalculator().estimate_hand_value(ids, win_id, melds=called, dora_indicators=indicators, config=config)
        applied_han,cost = result.han,result.cost
        yakuman = [y for y in result.yaku or [] if y.is_yakuman]
        if result.error is None and yakuman and not state.rules.composite_yakuman:
            applied_han = max(y.han_closed if all(m.closed for m in melds) else y.han_open or y.han_closed for y in yakuman)
            cost = ScoresCalculator().calculate_scores(applied_han,result.fu,config,is_yakuman=True)
        return {"legal_yaku": result.error is None, "error": result.error, "han": applied_han, "fu": result.fu,
                "cost": cost, "yaku": [y.name for y in result.yaku or []], "certainty": "known_context_only",
                "unknown": ["未揭示里宝牌", "责任支付需以服务端为准"] + ([] if actual else ["未来一发/岭上/海底/天和/地和等事件役未计入"])}
    except (ValueError, StopIteration):
        return {"legal_yaku": None, "certainty": "unknown", "error": "tile_or_context_inconsistency"}


def hand_features(state, hand, melds=None):
    melds = melds if melds is not None else state.players[state.self_seat].melds
    st = shanten(hand, len(melds))
    feature = {"after_phase": "waiting_draw", "shanten": st, "ukeire": [], "routes": routes(hand, melds, state)}
    visible = visible_counts(state)
    own = counts(hand)
    for index in range(34):
        if own[index] >= 4 or visible[index] >= 4:
            continue
        tile = tile_name(index)
        if shanten(hand + [tile], len(melds))["min"] < st["min"]:
            item = {"tile": tile, "visible_remaining": max(0, 4 - visible[index])}
            if st["min"] == 0:
                item["ron"] = score(state, hand, tile, melds)
                item["tsumo"] = score(state, hand, tile, melds, tsumo=True)
                if all(m.closed for m in melds):
                    item["riichi_ron"] = score(state, hand, tile, melds, riichi=True)
            feature["ukeire"].append(item)
    waits = {tile_index(w["tile"]) for w in feature["ukeire"]} if st["min"] == 0 else set()
    feature["discard_furiten"] = bool(waits & {tile_index(r.tile) for r in state.players[state.self_seat].river})
    feature["furiten"] = state.furiten
    feature["has_guaranteed_yaku"] = None  # Conditional routes are never promoted to completed yaku.
    return feature


def _consumptions(hand, combo):
    names = combo.split("|") if isinstance(combo, str) else combo
    pools = [[i for i, t in enumerate(hand) if t == tile] for tile in names]
    return sorted({tuple(sorted(ids)) for ids in product(*pools) if len(set(ids)) == len(ids)}) if pools else []


def legal_actions(state: Snapshot) -> list[LegalAction]:
    op = state.operation
    if op is None or not state.consistent or state.rules.unsupported:
        return []
    result = []
    own_melds = state.players[state.self_seat].melds
    feature_cache = {}
    for option in op.raw.get("operation_list", []):
        raw_type = option.get("type", 0)
        if raw_type not in KINDS:
            raise ValueError("unsupported server operation")
        kind = KINDS[raw_type]
        combos = option.get("combination", [])
        candidates = []
        if kind in ("discard", "riichi"):
            # type=1 combinations are forbidden kuikae tiles, not allowed choices.
            restricted = {tile_index(t) for t in combos} if kind == "discard" else set(combos)
            for index, tile in enumerate(state.hand):
                permitted = tile_index(tile) not in restricted if kind == "discard" else tile in restricted
                if kind == "discard" and state.players[state.self_seat].riichi == "established":
                    permitted = permitted and index == state.drawn_index
                if permitted:
                    hand = state.hand[:index] + state.hand[index + 1:]
                    key = tuple(sorted(hand))
                    if key not in feature_cache:
                        feature_cache[key] = hand_features(state, hand)
                    feature = feature_cache[key]
                    if kind == "riichi" and (feature["shanten"]["min"] != 0 or any(not m.closed for m in own_melds)):
                        raise ValueError("server/local riichi mismatch")
                    candidates.append((index, [], None, feature))
        elif kind in ("chi", "pon", "daiminkan", "ankan"):
            groups = []
            for combo in combos:
                names = combo.split("|")
                if kind == "ankan" and len(names) == 1:
                    groups.append(tuple(i for i, t in enumerate(state.hand) if tile_index(t) == tile_index(names[0])))
                else:
                    groups.extend(_consumptions(state.hand, combo))
            if kind == "ankan" and not groups:
                groups = [tuple(i for i, t in enumerate(state.hand) if tile_index(t) == k)
                          for k, n in Counter(map(tile_index, state.hand)).items() if n == 4]
            for indices in sorted(set(groups)):
                expected = 4 if kind == "ankan" else 3 if kind == "daiminkan" else 2
                if len(indices) != expected:
                    raise ValueError("server meld combination size mismatch")
                after = [t for i, t in enumerate(state.hand) if i not in indices]
                if kind in ("ankan", "daiminkan"):
                    feature = {"after_phase": "awaiting_replacement", "shanten": None, "unknown": ["replacement_tile"]}
                else:
                    offer = state.context.get('last_discard')
                    prospective = own_melds
                    forbidden = set()
                    if offer:
                        called = tile_index(offer['tile'])
                        forbidden.add(called)
                        consumed = [state.hand[i] for i in indices]
                        if kind == 'chi':
                            group = sorted(map(tile_index,consumed))
                            if called < group[0] and group[-1] % 9 < 8:
                                forbidden.add(group[-1]+1)
                            if called > group[-1] and group[0] % 9 > 0:
                                forbidden.add(group[0]-1)
                        prospective = own_melds + [Meld(id='projection',actor=state.self_seat,kind=kind,
                            tiles=[*consumed,offer['tile']],source_seat=offer['seat'],called_tile=offer['tile'],
                            event_id=op.window_id)]
                    feature = {"after_phase": "awaiting_discard", "shanten": None,
                               "followup": [{"tile": t, "shanten": shanten(after[:i] + after[i+1:], len(own_melds)+1),
                                             'standard_kuikae_allowed':tile_index(t) not in forbidden if offer else None}
                                            for i, t in enumerate(after)],
                               "followup_legality": "local_projection_requires_next_server_window", "no_yaku_risk": "check_open_routes",
                               'routes':routes(after,prospective,state) if offer else [],
                               'route_certainty':'known_consumption_and_offered_tile' if offer else 'offer_missing'}
                candidates.append((None, list(indices), None, feature))
        elif kind == "kakan":
            for meld in own_melds:
                if meld.kind == "pon":
                    for index, tile in enumerate(state.hand):
                        if tile_index(tile) == tile_index(meld.tiles[0]) and any(tile in c.split("|") for c in combos):
                            candidates.append((None, [index], meld.id, {"after_phase": "awaiting_replacement", "shanten": None}))
        elif kind in ("ron", "tsumo"):
            win = state.hand[state.drawn_index] if kind == "tsumo" and state.drawn_index is not None else (state.context.get("last_kan") or state.context.get("last_discard") or {}).get("tile")
            hand = [t for i, t in enumerate(state.hand) if kind != "tsumo" or i != state.drawn_index]
            value = score(state, hand, win, tsumo=kind == "tsumo", actual=True) if win else {"certainty": "unknown"}
            candidates.append((None, [], None, {"after_phase": "terminal", "server_offered_win": True, "value": value}))
        else:
            candidates.append((None, [], None, {"after_phase": "round_end", "shanten": None}))
        if not candidates:
            raise ValueError("server choice could not be represented")
        for tile, consumed, meld_id, feature in candidates:
            result.append(LegalAction(id=op.window_id + f":a{len(result)}", window_id=op.window_id,
                                      state_version=state.state_version, kind=kind, tile_index=tile,
                                      consume_indices=consumed, meld_id=meld_id, features=feature, raw_type=raw_type))
    if any(a.kind not in ("discard", "riichi") for a in result) and state.phase.value in ("response", "awaiting_replacement"):
        result.append(LegalAction(id=op.window_id + f":a{len(result)}", window_id=op.window_id, state_version=state.state_version,
                                  kind="pass", raw_type=0, features={"after_phase": "waiting", "shanten": shanten(state.hand, len(own_melds)), "furiten_effect": "possible_after_declining_ron"}))
    return result
