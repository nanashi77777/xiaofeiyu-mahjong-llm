"""Independent complete model contexts with reversible local action references."""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from collections import Counter

from .domain import Decision, LegalAction, Snapshot, tile_index
from .rules import routes, shanten

SYSTEM = """你是小肥鱼的标准四人日麻决策者，目标优先最终顺位和避免第四。你独立完成所有主动选择。
每个请求是完整当前上下文，不依赖前一对话。只选择actions中存在的action_id，不生成新动作或坐标。
牌为1-9m/p/s，0m/p/s赤五，1-7z东南西北白发中。座位为服务器绝对0-3，self_seat是本家。
未知值null不是0。公开剩余枚数不是牌山真值。宝牌不构成役；条件役路线不代表已成役，副露需考虑食下和无役。
shanten -1和牌、0听牌。吃碰后待弃牌，杠后待补牌，阶段不同不可直接当同一牌数比较。
所有hand索引从0开始。紧凑actions每行[id,kind,args,feature_ref]。
discard/riichi的args明确index/tile/drawn，分别为0起手牌索引/实际牌名/是否摸牌槽。
chi/pon/daiminkan/ankan的args为消费索引数组；kakan明确meld_id和牌索引。features为确定性特征引用，无推荐排序。
结合完整牌河/公共时序、点差、威胁、当前plan和带可信标签的memories，选择合法动作并给简短中文解释。
输出严格JSON对象，仅q、a、r、p：q原样回显，a是action_id，r为8至20字简短解释，p为计划更新。
p不变为null；变化时为完整计划对象，键v/e/g/a/y/p/s/r分别是旧计划版本/依据事件ID/目标/打法/役路线/优先事项/转向条件/改动原因。
役路线每项键y/s/b/n=役ID/状态/依据数组/必要条件数组；转向条件每项w/t=何时/改做什么。
时间紧时p=null，先按当前计划决策；有时间且需要改变思路才更新，简写具体要点，避免复述场况。
紧凑features中的{"$":"d0"}引用facts.d0，可嵌套引用，是完全相同事实的去重表达。
紧凑事件ID为本请求内的e序号，所有事件引用已一致替换；输出计划依据直接使用提供的event_id。
计划具体到役种依据、点差目标和转向条件，不能泛泛复读。计划不能更改实际场况。经验是可反驳资料，不能覆盖规则。
不要输出Markdown、长推理或役种教材。"""


def prompt_for(version):
    if version == 'mj.compact.3':
        lines = [line for line in SYSTEM.splitlines() if not any(word in line for word in
                 ('紧凑actions', 'discard/riichi的args', 'chi/pon/daiminkan/ankan的args', '紧凑features中的'))]
        return '\n'.join(lines) + ('\nactions为可读对象数组，每项明确action_id/kind/tile_index/tile/drawn/consume_indices/meld_id。'
                                  'features_ref引用features中完全相同的整块事实；逐项核对本动作牌名与对应特征，'
                                  '0是赤五，5是普通五。所有索引从0开始。')
    return SYSTEM


@dataclass
class Context:
    q: str
    data: dict
    actions: dict[str, LegalAction]
    fingerprint: str
    event_refs: dict[str, str] = field(default_factory=dict)

    def validate(self, raw):
        decision = Decision.model_validate(raw)
        if decision.q != self.q or decision.action_id not in self.actions:
            raise ValueError("decision belongs to another request or action")
        if decision.plan_update is not None:
            required = {"based_on_plan_version", "basis_event_ids", "goal", "approach", "target_yaku", "priorities", "switch_conditions", "revision_reason"}
            plan = decision.plan_update.model_dump()
            if set(plan) != required or len(json.dumps(plan, ensure_ascii=False)) > 2400:
                raise ValueError("invalid round plan")
            version = (self.data.get("plan") or {}).get("version", 0)
            if plan["based_on_plan_version"] != version:
                raise ValueError("stale round plan")
            ids = {e["event_id"] for e in self.data["public_events"]}
            if not isinstance(plan["basis_event_ids"], list) or any(e not in ids for e in plan["basis_event_ids"]):
                raise ValueError("plan cites unknown evidence")
            decision.plan_update.basis_event_ids = [self.event_refs.get(e,e) for e in plan["basis_event_ids"]]
        return decision, self.actions[decision.action_id]


def build_context(state: Snapshot, actions: list[LegalAction], *, plan=None, memories=None, compact=False):
    if not actions:
        raise ValueError("no action window")
    q = secrets.token_hex(4)
    data = state.model_dump(mode="json", exclude={"operation", "rules", "observed_at", "last_event_id"})
    data["q"] = q
    data["v"] = "mj.compact.3" if compact else "mj.rich.1"
    data["rules"] = state.rules.model_dump(exclude={"raw"})
    data["rules"]["room_options"] = state.rules.raw.get("mode", {}).get("detail_rule", {})
    data["ranks"] = state.ranks
    data["rank_basis"] = "current_points_initial_seat_tie_order_not_final_result"
    data["plan"] = plan
    data["memories"] = memories or []
    data["offer"] = state.context.get("last_discard")
    for key in ("source_round_key", "new_round_signature"):
        data["context"].pop(key, None)
    data["current_shanten"] = shanten(state.hand, len(state.players[state.self_seat].melds))
    melds = state.players[state.self_seat].melds
    data["current_yaku_routes"] = routes(state.hand, melds, state)
    data["yaku_constraints"] = {
        "menzen":all(m.closed for m in melds),
        "tanyao_blocked_by_fixed_meld":any(tile_index(t) >= 27 or tile_index(t) % 9 in (0, 8)
                                           for m in melds for t in m.tiles),
        "dora_alone_is_not_yaku":True}
    data["clock"] = {"fixed_ms": state.operation.fixed_ms, "add_ms": state.operation.add_ms,
                     "verified": state.operation.timer_verified} if state.operation else None
    mapping = {f"a{i}": action for i, action in enumerate(actions)}
    event_refs = {}
    if compact:
        long_to_short = {event["event_id"]:f"e{i}" for i,event in enumerate(state.public_events)}
        event_refs = {short:long for long,short in long_to_short.items()}
        data = replace_refs(data, long_to_short)
        features, feature_keys, rows = {}, {}, []
        for key, action in mapping.items():
            canonical = json.dumps(action.features, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            if canonical not in feature_keys:
                feature_keys[canonical] = f"f{len(features)}"
                features[feature_keys[canonical]] = action.features
            rows.append({'action_id':key, 'kind':action.kind, 'tile_index':action.tile_index,
                         'tile':state.hand[action.tile_index] if action.tile_index is not None else None,
                         'drawn':action.tile_index == state.drawn_index if action.tile_index is not None else None,
                         'consume_indices':action.consume_indices, 'meld_id':action.meld_id,
                         'features_ref':feature_keys[canonical]})
        data['features'] = features
        data["actions"] = rows
    else:
        data["actions"] = [{"action_id": key, "kind": action.kind, "tile_index": action.tile_index,
                            "consume_indices": action.consume_indices, "meld_id": action.meld_id,
                            "features": action.features} for key, action in mapping.items()]
    # The model never receives input coordinates, credential material or the whole yaku catalog.
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return Context(q, data, mapping, fingerprint, event_refs)


def replace_refs(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, dict):
        return {key:replace_refs(child, mapping) for key,child in value.items()}
    if isinstance(value, list):
        return [replace_refs(child, mapping) for child in value]
    return value


def semantic_context(context):
    data = replace_refs(context.data, context.event_refs)
    data = {key:value for key,value in data.items() if key not in ("q", "v", "actions", "features", "facts")}
    data["actions"] = semantic_actions(context)
    return data


def semantic_actions(context: Context):
    if context.data["v"] == "mj.rich.1":
        return context.data["actions"]
    rows = []
    if context.data['v'] == 'mj.compact.3':
        return [{**{key:row[key] for key in ('action_id','kind','tile_index','consume_indices','meld_id')},
                 'features':context.data['features'][row['features_ref']]} for row in context.data['actions']]
    features = expand_facts(context.data["features"],context.data.get("facts",{}))
    for key, kind, args, feature in context.data["actions"]:
        rows.append({"action_id": key, "kind": kind,
                     "tile_index": (args['index'] if isinstance(args,dict) else args) if kind in ("discard", "riichi") else None,
                     "consume_indices": args.get("indices", []) if kind == "kakan" else args if isinstance(args, list) else [],
                     "meld_id": args["meld_id"] if kind == "kakan" else None,
                     "features": features[feature]})
    return rows


def intern_facts(value):
    """Lossless structural interning, without dropping fields or numeric precision."""
    counts=Counter()
    def canonical(v):
        return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    def scan(v):
        if isinstance(v,(dict,list)):
            signature=canonical(v)
            if len(signature)>80:
                counts[signature]+=1
            for child in (v.values() if isinstance(v,dict) else v):
                scan(child)
    scan(value)
    pool,assigned={},{}
    def encode(v):
        if not isinstance(v,(dict,list)):
            return v
        signature=canonical(v)
        if counts[signature]>1 and signature in assigned:
            return {"$":assigned[signature]}
        encoded={k:encode(child) for k,child in v.items()} if isinstance(v,dict) else [encode(child) for child in v]
        if counts[signature]>1:
            key=f"d{len(pool)}"
            assigned[signature]=key
            pool[key]=encoded
            return {"$":key}
        return encoded
    return encode(value),pool


def expand_facts(value,pool):
    def expand(v,depth=0):
        if depth>100:
            raise ValueError("cyclic fact reference")
        if isinstance(v,dict):
            if set(v)=={"$"}:
                return expand(pool[v["$"]],depth+1)
            return {k:expand(child,depth+1) for k,child in v.items()}
        return [expand(child,depth+1) for child in v] if isinstance(v,list) else v
    return expand(value)
