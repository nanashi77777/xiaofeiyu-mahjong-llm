"""One fresh operation → one model choice → physical input → observed receipt."""
from __future__ import annotations

import asyncio
import time
import uuid

from .memory import Memories
from .domain import Decision, Receipt, RoundPlanUpdate
from .input_lease import finish_input
from .native import InputDeferred
from .rules import legal_actions
from .store import utcnow
from .timing import TimeBank
from .wire import build_context


class Coordinator:
    def __init__(self, settings, engine, model, executor=None):
        self.settings, self.engine, self.model, self.executor = settings, engine, model, executor
        self.store = engine.store
        self.enabled = False
        self.shadow = True
        self.task = None
        self.used_windows = set()
        self.pending = None
        self.plan = None
        self.round_uid = None
        self.match_id = None
        self.frozen_memories = []
        self.bank = TimeBank(settings.base_seconds, settings.bank_seconds, settings.execution_margin)
        self.status = "stopped"

    def record_receipt(self, receipt, match_id, round_uid):
        validated = Receipt.model_validate(receipt).model_dump(mode='json',exclude_none=True)
        return self.store.record('receipt',validated,match_id,round_uid)

    def active(self):
        return bool(self.task and not self.task.done())

    async def input_observed(self,stage,window_id,payload,ack_id):
        if stage=="request" and not self.pending and self.active():
            self.task.cancel()  # manual/client input supersedes a still-thinking model
        if not self.pending or self.pending["receipt"]["action"]["window_id"]!=window_id:
            return
        action=self.pending["receipt"]["action"]
        if stage=="accepted" and action["kind"]=="pass" and payload.get("cancel_operation"):
            if self.pending["receipt"]["status"] == "validated":
                self.pending["early_ack"] = (payload, ack_id)
                return
            elapsed=time.monotonic()-self.pending["start"]
            receipt={**self.pending["receipt"],"status":"server_confirmed","actual_ack_id":ack_id,
                     "confirmation_source":"server_rpc_ack_for_cancel_operation","elapsed_ms":elapsed*1000}
            state=self.pending["state"]
            self.record_receipt(receipt,state.match_id,state.round_uid)
            self.adopt_plan(self.pending)
            remaining=self.bank.charge(window_id,elapsed,fixed_ms=state.operation.fixed_ms if self.settings.timer_verified else None)
            self.store.record("time_bank",{"remaining":remaining,"window_id":window_id},state.match_id,state.round_uid)
            self.pending=None
            self.status="pass_acknowledged"
        elif stage=="rejected":
            self.enabled=False
            self.status="paused_server_rejected_input"

    async def stop(self, reason="paused"):
        self.enabled = False
        self.status = reason
        if self.executor:
            self.executor.mouse.stop.set()
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.pending:
            pending, self.pending = self.pending, None
            state = pending['state']
            self.record_receipt({**pending['receipt'], 'status':'cancelled', 'reason':reason},
                              state.match_id, state.round_uid)

    async def start(self, shadow=True):
        if self.executor and self.executor.lease.owner is not None:
            raise ValueError("input already owned")
        if not self.settings.api_key.get_secret_value():
            raise ValueError("model API key is missing")
        self.enabled, self.shadow = True, shadow
        if self.executor:
            self.executor.mouse.stop.clear()
        self.status = "observing_shadow" if shadow else "observing_active"
        await self.changed(self.engine.state, None)

    async def changed(self, state, event):
        if self.task and not self.task.done():
            # Every actual event invalidates a decision made against older facts.
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if not state:
            return
        if self.pending and event:
            self.confirm(state, event)
        if state.match_id != self.match_id:
            self.match_id = state.match_id
            # Frozen once per match; later reviews affect the next match.
            existing = self.store.records("memory_freeze", state.match_id, 1)
            self.frozen_memories = existing[0]["payload"]["memories"] if existing else Memories(self.store).freeze()
            if not existing:
                self.store.record("memory_freeze", {"memories":self.frozen_memories}, state.match_id)
        if state.round_uid != self.round_uid:
            self.round_uid, self.plan = state.round_uid, None
            self.bank.enter_round(state.round_uid)
            saved = self.store.records("time_bank", state.match_id, 1000)
            prior = next((r for r in saved if r["round_uid"] == state.round_uid), None)
            if prior:
                self.bank.remaining = min(self.bank.remaining, prior["payload"]["remaining"])
            plans = self.store.records("round_plan", state.match_id, 1000)
            self.plan = next((r["payload"] for r in plans if r["round_uid"] == state.round_uid), None)
        op = state.operation
        if not self.enabled or not op or op.historical or state.lifecycle != "active":
            return
        if not state.consistent or state.round_completeness != "full" or state.rules.unsupported:
            self.status = "paused_incomplete_state_or_rules"
            return
        if op.window_id in self.used_windows:
            return
        self.used_windows.add(op.window_id)
        self.task = asyncio.create_task(self.decide(state.model_copy(deep=True)))

    async def decide(self, state):
        op = state.operation
        start = op.received_mono
        try:
            self.status = "deciding"
            rules_start = time.monotonic()
            if self.settings.integration_test and op.fixed_ms and op.fixed_ms >= 300000 and op.add_ms == 0:
                # Explicit user-authorized 300+0 integration run; excluded from speed acceptance.
                deadline = op.received_mono + op.fixed_ms/1000 - self.settings.execution_margin
            else:
                deadline = self.bank.deadline(op, verified=self.settings.timer_verified)
            actions = await asyncio.to_thread(legal_actions, state)
            rules_done = time.monotonic()
            if not actions:
                raise ValueError("no legal candidate representation")
            memory_store = Memories(self.store)
            from .rules import routes
            yaku_ids = [r['id'] for r in routes(state.hand,state.players[state.self_seat].melds,state)]
            recalled = memory_store.recall(self.frozen_memories,state,yaku_ids)
            self.store.record('memory_recall',{'window_id':op.window_id,
                              'revisions':[{'id':m['id'],'revision':m['revision']} for m in recalled]},state.match_id,state.round_uid)
            context = build_context(state, actions, plan=self.plan, memories=memory_store.projection(recalled),
                                    compact=self.settings.compact_validated)
            context.data["clock"]["local_bank_remaining"] = self.bank.remaining
            context.data["clock"]["verified"] = self.settings.timer_verified
            context.data["clock"]["decision_seconds_remaining"] = max(0, deadline-time.monotonic())
            context.data['clock']['soft_seconds_remaining'] = max(0,self.settings.base_seconds-(time.monotonic()-start))
            model_start = time.monotonic()
            decision, action, metrics = await self.model.decide(context, deadline)
            metrics.update(rules_ms=(rules_done-rules_start)*1000,
                           wire_ms=(model_start-rules_done)*1000,
                           read_to_model_ms=(model_start-start)*1000)
            current = self.engine.state
            if not current or not current.operation or current.state_version != state.state_version or time.monotonic() >= deadline:
                raise TimeoutError("decision superseded or expired")
            proposed_plan = None
            if decision.plan_update:
                proposed_plan = {**decision.plan_update.model_dump(), 'schema_version':RoundPlanUpdate.contract_version,
                                 "version":(self.plan or {}).get("version", 0)+1,
                                 "round_uid":state.round_uid, "state_version":state.state_version}
                self.store.record("round_plan_proposed", proposed_plan, state.match_id, state.round_uid)
            self.store.record("decision", {'schema_version':Decision.contract_version, "decision":decision.model_dump(), "action":action.model_dump(),
                                           "context_fingerprint":context.fingerprint, "wire_version":context.data["v"],
                                           "metrics":metrics, "shadow":self.shadow}, state.match_id, state.round_uid)
            if self.shadow:
                self.status = "shadow_decision_recorded"
                return
            if not self.executor:
                raise ValueError("no physical executor")
            receipt = {"id":uuid.uuid4().hex, "action":action.model_dump(), "status":"validated", "created_at":utcnow()}
            self.pending = {"receipt":receipt, "state":state, "start":start, "proposed_plan":proposed_plan}
            async def send():
                try:
                    input_start = time.monotonic()
                    clicked = await self.executor.execute(state, action, deadline + self.settings.execution_margin)
                    receipt.update(status="mouse_sent", clicked_mono=clicked,
                                   input_ms=(clicked-input_start)*1000, read_to_mouse_ms=(clicked-start)*1000)
                    self.record_receipt(receipt,state.match_id,state.round_uid)
                    self.status = "awaiting_actual_event"
                    if self.pending and self.pending.get("early_ack"):
                        payload, ack_id = self.pending.pop("early_ack")
                        await self.input_observed("accepted", action.window_id, payload, ack_id)
                except Exception:
                    # A compound click may already have opened a menu. Preserve
                    # uncertainty, stop further input, and do not compare the
                    # next unrelated event to an unsent action.
                    self.record_receipt({**receipt, "status":"execution_uncertain"},
                                      state.match_id, state.round_uid)
                    self.pending = None
                    self.enabled = False
                    raise
            # A server event may arrive during mouse release. Finish the input
            # and its journal write before changed() confirms the actual event.
            await finish_input(send())
        except asyncio.CancelledError:
            self.store.record("decision_cancelled", {"window_id":op.window_id}, state.match_id, state.round_uid)
            raise
        except Exception as error:
            self.status = "decision_or_input_deferred_" + type(error).__name__
            self.store.record("decision_failure", {"window_id":op.window_id, "error_type":type(error).__name__,
                                                   "detail":str(error) if isinstance(error, (InputDeferred, TimeoutError)) else None,
                                                   "elapsed_ms":(time.monotonic()-start)*1000}, state.match_id, state.round_uid)

    def confirm(self, state, event):
        pending = self.pending
        action = pending["receipt"]["action"]
        kind, data = action["kind"], event.data
        relevant = data.get("seat") == state.self_seat or event.kind in ("ActionHule", "ActionNoTile", "ActionLiuJu", "ActionNewRound", "NotifyGameEndResult")
        if kind == "pass":
            relevant = True
        if not relevant:
            return
        if kind == "pass":
            # Only the matching cancel_operation RPC acknowledgement confirms
            # a pass; the next draw/discard is not evidence of our input.
            return
        if kind in ("discard", "riichi"):
            tile = pending["state"].hand[action["tile_index"]]
            matches = event.kind == "ActionDiscardTile" and data.get("tile") == tile
            if kind == "riichi":
                matches = matches and bool(data.get("is_liqi") or data.get("is_wliqi"))
        elif kind in ("chi", "pon", "daiminkan"):
            consumed = [pending["state"].hand[i] for i in action["consume_indices"]]
            actual = [t for t,s in zip(data.get("tiles", []), data.get("froms", []), strict=True) if s == state.self_seat]
            matches = event.kind == "ActionChiPengGang" and data.get("type") == {"chi":0,"pon":1,"daiminkan":2}[kind] and sorted(consumed) == sorted(actual)
        elif kind in ("ankan", "kakan"):
            from .domain import tile_index
            expected = pending["state"].hand[action["consume_indices"][0]]
            actual = data.get("tiles")
            actual = actual[0] if isinstance(actual, list) and actual else actual
            matches = (event.kind == "ActionAnGangAddGang" and data.get("type") == (3 if kind == "ankan" else 2)
                       and isinstance(actual, str) and tile_index(actual) == tile_index(expected))
        elif kind in ("tsumo", "ron"):
            matches = event.kind == "ActionHule" and any(w.get("seat",0) == state.self_seat for w in data.get("hules",[]))
        elif kind == "abort9":
            matches = event.kind == "ActionLiuJu"
        else:
            matches = kind == "pass" and event.kind not in ("ActionChiPengGang", "ActionAnGangAddGang")
        receipt = {**pending["receipt"], "status":"server_confirmed" if matches else "mismatch", "actual_event_id":event.id,
                   "confirmed_at":utcnow(), "elapsed_ms":(event.received_mono-pending["start"])*1000}
        self.record_receipt(receipt,state.match_id,pending['state'].round_uid)
        if matches:
            self.adopt_plan(pending)
        remaining = self.bank.charge(action['window_id'], max(0,event.received_mono-pending['start']),
                                     fixed_ms=pending['state'].operation.fixed_ms if self.settings.timer_verified else None)
        self.store.record("time_bank", {"remaining":remaining,"window_id":action["window_id"]}, state.match_id, pending["state"].round_uid)
        self.pending = None
        if not matches:
            self.enabled, self.status = False, "paused_input_mismatch"

    def adopt_plan(self, pending):
        proposed = pending.get("proposed_plan")
        if proposed:
            state = pending["state"]
            self.store.record("round_plan", proposed, state.match_id, state.round_uid)
            if self.round_uid == state.round_uid:
                self.plan = proposed
