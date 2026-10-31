"""Rebuild win attribution from archived facts without duplicating persistence.

Seats are absolute server seats. Only the immediately preceding Mahjong action
can supply a win; score deltas and responsibility payments are never evidence.
"""
from __future__ import annotations

from collections.abc import Iterable

from .domain import tile_index


def _tile(value):
    if isinstance(value, str):
        try:
            tile_index(value)
            return value
        except ValueError:
            pass
    return None


def _seat(value):
    return value if type(value) is int and 0 <= value < 4 else None


def win_outcomes(events: Iterable[dict]) -> list[dict]:
    """Project ordered rows containing id, match_id, round_uid, seq, kind, data.

    ObservedPass is transparent. Unknown actions and sequence gaps invalidate
    the prior source, and round/match boundaries never inherit one. For an
    unconfirmed source its reference is a diagnostic candidate, not attribution.
    """
    results, seen = [], set()
    source, scope, previous_seq = None, None, None
    for event in events:
        identity = (event['match_id'], event['id'])
        if identity in seen:
            continue
        seen.add(identity)
        current_scope = (event['match_id'], event.get('round_uid'))
        if current_scope != scope or (previous_seq is not None and event['seq'] != previous_seq + 1):
            source = None
        scope, previous_seq = current_scope, event['seq']
        kind, data = event['kind'], event['data']
        if kind == 'ActionHule':
            for index, win in enumerate(data.get('hules', [])):
                results.append(_outcome(event, index, win, source))
            source = None
        elif kind in ('ActionNewRound', 'ActionDealTile', 'ActionDiscardTile', 'ActionAnGangAddGang'):
            source = event
        elif kind != 'ObservedPass':
            source = None
    return results


def _outcome(event, index, win, source):
    tsumo = bool(win.get('zimo', False))  # Proto3 omits false and seat zero.
    winner, tile = _seat(win.get('seat', 0)), _tile(win.get('hu_tile'))
    result = {'id':f"{event['id']}:{index}", 'event_id':event['id'], 'round_id':event.get('round_uid'),
              'winner_seat':winner, 'kind':'tsumo' if tsumo else 'ron', 'tile':tile, 'from_seat':None,
              'source_event_id':None, 'source_kind':None, 'source_status':'unknown', 'issues':[]}
    issues = result['issues']
    if winner is None:
        issues.append('winner_seat_invalid')
    if 'hu_tile' in win and tile is None:
        issues.append('winning_tile_invalid')
    if source is None or event.get('round_uid') is None:
        issues.append('source_not_observed' if source is None else 'round_identity_missing')
    else:
        data, kind = source['data'], source['kind']
        source_kind = {'ActionNewRound':'initial_deal', 'ActionDealTile':'draw',
                       'ActionDiscardTile':'discard'}.get(kind)
        if kind == 'ActionAnGangAddGang':
            source_kind = {2:'kakan', 3:'ankan'}.get(data.get('type'))
        actor = _seat(data.get('ju' if kind == 'ActionNewRound' else 'seat', 0))
        source_tile = _tile(data.get('tiles' if kind == 'ActionAnGangAddGang' else 'tile'))
        result.update(source_event_id=source['id'], source_kind=source_kind)
        expected = source_kind in ('draw', 'initial_deal') if tsumo else source_kind in ('discard', 'kakan', 'ankan')
        if not expected:
            issues.append('source_action_mismatch')
        if actor is None or winner is None or (actor != winner if tsumo else actor == winner):
            issues.append('source_seat_mismatch')
        # Concealed-kan tiles identify a TYPE, not an individual red/normal five.
        # hu_tile, if supplied, retains the winning tile's actual color.
        if tile and source_tile and (tile_index(tile) != tile_index(source_tile) if source_kind == 'ankan'
                                     else tile != source_tile):
            issues.append('source_tile_mismatch')
        if tile is None and 'hu_tile' not in win and expected:
            if source_kind != 'ankan' or (source_tile and tile_index(source_tile) not in (4, 13, 22)):
                result['tile'] = source_tile
        if not tsumo and source_tile is None:
            issues.append('source_tile_not_observed')
        if not issues:
            result['source_status'] = 'confirmed'
            result['from_seat'] = None if tsumo else actor
        else:
            result['source_status'] = 'conflict' if any(i.endswith(('mismatch', 'invalid')) for i in issues) else 'unknown'
    if result['tile'] is None:
        issues.append('winning_tile_not_observed')
    return result


def win_summary(outcomes: list[dict], self_seat: int) -> dict:
    """Count a multi-ron deal-in once, while retaining each winning opponent."""
    dealt_in = [w for w in outcomes if w['kind'] == 'ron' and w['from_seat'] == self_seat
                and w['source_status'] == 'confirmed']
    return {'self_seat':self_seat,
            'ron':sum(w['kind'] == 'ron' and w['winner_seat'] == self_seat for w in outcomes),
            'tsumo':sum(w['kind'] == 'tsumo' and w['winner_seat'] == self_seat for w in outcomes),
            'deal_in_events':len({w['event_id'] for w in dealt_in}), 'deal_in_winners':len(dealt_in),
            'unattributed_ron':sum(w['kind'] == 'ron' and w['source_status'] != 'confirmed' for w in outcomes)}
