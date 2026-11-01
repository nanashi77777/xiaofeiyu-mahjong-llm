"""One fresh operation → one model choice → physical input → observed receipt."""
from __future__ import annotations

import asyncio
import time
import uuid

from .memory import Memories
from .domain import Decision, Receipt, RoundPlanUpdate
from .input_lease import finish_input
from .input_origin import input_origin
from .native import InputDeferred
from .model import ModelFailure
from .recovery import Recovery
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
        self.generation = 0
        self.annotation_tasks = set()
        self.recovery = Recovery(settings.failure_window_limit)
        self.confirmation_task = None
        self.connection_task = None
        self.confirmed_windows = set()

    def recovery_record(self, state, stage, outcome, **fields):
        self.store.record('recovery', {'stage':stage,'outcome':outcome,
            'window_id':state.operation.window_id if state.operation else None,
            **fields}, state.match_id, state.round_uid)

    def failed_window(self, state, reason):
        tripped = self.recovery.failure(state.match_id, state.operation.window_id)
        elapsed = max(0,time.monotonic()-state.operation.received_mono)
        self.bank.charge(state.operation.window_id,elapsed,
                         fixed_ms=state.operation.fixed_ms if self.settings.timer_verified else None)
        self.store.record('time_bank',self.bank.checkpoint(state.operation.window_id),state.match_id,state.round_uid)
        self.recovery.phase = 'circuit_open' if tripped else 'waiting_next_window'
        self.recovery_record(state,'window','circuit_open' if tripped else 'waiting_next_window',
                             reason=reason,consecutive_failures=self.recovery.streak)
        if self.enabled:
            if tripped:
                self.enabled = False
                self.status = 'paused_repeated_window_failures'
            else:
                self.status = 'recovering_next_window'

    def confirmed_window(self, state):
        self.confirmed_windows.add((state.match_id,state.operation.window_id))
        if self.recovery.streak:
            self.recovery_record(state,'window','recovered',prior_failed_windows=self.recovery.streak)
        self.recovery.confirmed()
        if not self.enabled:
            self.recovery.phase = 'resolved_while_paused'

    def watch_confirmation(self, pending):
        if not self.enabled:
            return
        if self.confirmation_task and not self.confirmation_task.done():
            self.confirmation_task.cancel()
        self.confirmation_task = asyncio.create_task(self.wait_confirmation(pending,self.generation))

    async def wait_confirmation(self, pending, generation):
        await asyncio.sleep(self.settings.confirmation_wait_seconds)
        if generation != self.generation or not self.enabled or self.pending is not pending:
            return
        state = pending['state']
        self.record_receipt({**pending['receipt'],'status':'unknown','reason':'confirmation_wait_exhausted'},
                            state.match_id,state.round_uid)
        self.failed_window(state,'confirmation_wait_exhausted')
        self.enabled, self.status = False, 'paused_unconfirmed_input'
        self.recovery.phase = 'input_unknown'
        self.recovery_record(state,'confirmation','paused_no_repeat')
        # Retain pending for a late authoritative event; that event can resolve
        # the record but cannot re-enable input after this pause.

    async def wait_connection(self, state, generation):
        for attempt in range(self.settings.connection_wait_attempts):
            self.recovery_record(state,'connection','waiting_actual_sync',attempt=attempt+1)
            await asyncio.sleep(self.settings.connection_wait_seconds)
            if generation != self.generation or not self.enabled:
                return
            current = self.engine.state
            if current and current.lifecycle == 'active' and current.consistent and current.round_completeness == 'full':
                self.recovery_record(current,'connection','actual_sync_observed',attempt=attempt+1)
                self.recovery.phase = 'idle'
                return
        if generation == self.generation and self.enabled:
            self.enabled, self.status = False, 'paused_connection_recovery_exhausted'
            self.recovery.phase = 'connection_exhausted'
            self.recovery_record(state,'connection','exhausted')

    def record_receipt(self, receipt, match_id, round_uid):
        validated = Receipt.model_validate(receipt).model_dump(mode='json',exclude_none=True)
        return self.store.record('receipt',validated,match_id,round_uid)

    def active(self):
        return bool(self.task and not self.task.done())

    def input_stopped(self):
        if not self.executor:
            return False
        mouse = self.executor.mouse
        user = getattr(mouse,'user',None)
        if user and user.GetAsyncKeyState(0x1B)&0x8000:
            mouse.stop.set()
        return mouse.stop.is_set()

    async def input_observed(self,stage,window_id,payload,ack_id):
        if stage=="request" and not self.pending and self.active():
            self.task.cancel()  # manual/client input supersedes a still-thinking model
        if not self.pending or self.pending["receipt"]["action"]["window_id"]!=window_id:
            return
        action=self.pending["receipt"]["action"]
        automatic = input_origin(payload) == 'client_automatic'
        if stage == 'request':
            if ack_id:
                ids = self.pending['receipt'].setdefault('client_input_ids', [])
                if ack_id not in ids:
                    ids.append(ack_id)
            if automatic:
                self.pending['automatic_input'] = True
            return
        if stage == 'accepted' and (automatic or self.pending.get('automatic_input')):
            if self.pending['receipt']['status'] == 'validated':
                self.pending['early_ack'] = (payload, ack_id)
                return
            pending, self.pending = self.pending, None
            state = pending['state']
            elapsed = max(0, time.monotonic()-pending['start'])
            self.record_receipt({**pending['receipt'], 'status':'mismatch',
                'reason':'client_automatic_input_not_bot_confirmation', 'actual_ack_id':ack_id,
                'confirmed_at':utcnow(), 'elapsed_ms':elapsed*1000}, state.match_id,state.round_uid)
            self.bank.charge(window_id, elapsed, fixed_ms=state.operation.fixed_ms if self.settings.timer_verified else None)
            self.store.record('time_bank',self.bank.checkpoint(window_id),state.match_id,state.round_uid)
            self.enabled, self.status = False, 'paused_client_automatic_input'
            return
        if stage=="accepted" and action["kind"]=="pass" and payload.get("cancel_operation"):
            if self.pending["receipt"]["status"] == "validated":
                self.pending["early_ack"] = (payload, ack_id)
                return
            elapsed=time.monotonic()-self.pending["start"]
            receipt={**self.pending["receipt"],"status":"server_confirmed","actual_ack_id":ack_id,
                     "confirmation_source":"server_rpc_ack_for_cancel_operation","elapsed_ms":elapsed*1000}
            state=self.pending["state"]
            self.record_receipt(receipt,state.match_id,state.round_uid)
            self.confirmed_window(state)
            self.adopt_plan(self.pending)
            self.bank.charge(window_id,elapsed,fixed_ms=state.operation.fixed_ms if self.settings.timer_verified else None)
            self.store.record("time_bank",self.bank.checkpoint(window_id),state.match_id,state.round_uid)
            self.pending=None
            if self.enabled:
                self.status="pass_acknowledged"
        elif stage=="rejected":
            self.enabled=False
            self.status="paused_server_rejected_input"

    async def stop(self, reason="paused"):
        self.generation += 1
        self.enabled = False
        self.status = reason
        self.recovery.phase = 'stopped'
        if self.executor:
            self.executor.mouse.stop.set()
        watchers = [t for t in (self.confirmation_task,self.connection_task) if t and not t.done()]
        for watcher in watchers:
            watcher.cancel()
        await asyncio.gather(*watchers,return_exceptions=True)
        if self.executor:
            if hasattr(self.executor,'cancel_idle_observation'):
                await self.executor.cancel_idle_observation()
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        for task in list(self.annotation_tasks):
            task.cancel()
        await asyncio.gather(*self.annotation_tasks,return_exceptions=True)
        if self.pending:
            pending, self.pending = self.pending, None
            state = pending['state']
            self.record_receipt({**pending['receipt'], 'status':'cancelled', 'reason':reason},
                              state.match_id, state.round_uid)
        self.status, self.recovery.phase = reason, 'stopped'

    async def start(self, shadow=True):
        if self.pending:
            raise ValueError('unresolved input requires an actual receipt before restart')
        if self.executor and self.executor.lease.owner is not None:
            raise ValueError("input already owned")
        if not self.settings.api_key.get_secret_value():
            raise ValueError("model API key is missing")
        self.generation += 1
        self.enabled, self.shadow = True, shadow
        self.recovery.streak = 0  # Only an explicit start resets an open circuit.
        self.recovery.phase = 'idle'
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
        if self.enabled and self.input_stopped():
            self.enabled, self.status = False, 'paused_input_stopped'
            self.recovery.phase = 'stopped'
        if self.enabled and (not state.consistent or state.round_completeness != 'full' or state.rules.unsupported):
            self.enabled, self.status = False, 'paused_incomplete_state_or_rules'
            self.recovery.phase = 'invalid_facts'
            return
        if self.enabled and state.lifecycle == 'interrupted':
            self.status, self.recovery.phase = 'recovering_connection', 'waiting_connection'
            if not self.connection_task or self.connection_task.done():
                self.connection_task = asyncio.create_task(self.wait_connection(state,self.generation))
            return
        if self.connection_task and not self.connection_task.done() and state.lifecycle == 'active':
            self.connection_task.cancel()
            self.recovery_record(state,'connection','actual_sync_observed')
            self.recovery.phase = 'idle'
        if state.match_id != self.match_id:
            self.match_id = state.match_id
            self.used_windows.update(r['payload']['window_id'] for r in self.store.records('decision_window',state.match_id,10000))
            self.used_windows.update(r['payload']['action']['window_id'] for r in self.store.records('decision',state.match_id,10000))
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
                self.bank.restore(prior['payload'])
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
        if self.pending:
            # A new window never licenses replay of an unresolved old input.
            self.status = 'awaiting_actual_event'
            return
        self.used_windows.add(op.window_id)
        self.store.record('decision_window',{'window_id':op.window_id,'shadow':self.shadow},state.match_id,state.round_uid)
        self.task = asyncio.create_task(self.decide(state.model_copy(deep=True)))

    async def decide(self, state):
        op = state.operation
        start = op.received_mono
        choice = None
        annotation_started = False
        generation = self.generation
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
                                    compact=self.settings.compact_validated,
                                    tabled=self.settings.compact_validated and self.settings.table_wire_validated)
            context.data["clock"]["local_bank_remaining"] = self.bank.remaining
            context.data["clock"]["verified"] = self.settings.timer_verified
            context.data["clock"]["decision_seconds_remaining"] = max(0, deadline-time.monotonic())
            context.data['clock']['soft_seconds_remaining'] = max(0,self.settings.base_seconds-(time.monotonic()-start))
            model_start = time.monotonic()
            observation = (asyncio.create_task(self.executor.observe(state,actions,deadline))
                           if not self.shadow and self.executor and hasattr(self.executor,'observe') else None)
            action = None
            handoff_ms, handoff_mode = 0, None
            try:
                if self.settings.stream_actions:
                    choice = await self.model.begin_decision(context, deadline)
                    decision,action,metrics = None,choice.action,choice.metrics
                else:
                    decision, action, metrics = await self.model.decide(context, deadline)
            finally:
                if observation:
                    handoff_start = time.monotonic()
                    if hasattr(self.executor, 'finish_observation'):
                        handoff_mode = await self.executor.finish_observation(observation, state, action, deadline)
                    else:
                        observation.cancel()
                        await asyncio.gather(observation,return_exceptions=True)
                    handoff_ms = (time.monotonic()-handoff_start)*1000
            metrics.update(rules_ms=(rules_done-rules_start)*1000,
                           observation_handoff_ms=handoff_ms, observation_handoff_mode=handoff_mode,
                           wire_ms=(model_start-rules_done)*1000,
                           read_to_model_ms=(model_start-start)*1000)
            current = self.engine.state
            if not current or not current.operation or current.state_version != state.state_version or time.monotonic() >= deadline:
                raise TimeoutError("decision superseded or expired")
            proposed_plan = None
            if decision and decision.plan_update:
                proposed_plan = {**decision.plan_update.model_dump(), 'schema_version':RoundPlanUpdate.contract_version,
                                 "version":(self.plan or {}).get("version", 0)+1,
                                 "round_uid":state.round_uid, "state_version":state.state_version}
                self.store.record("round_plan_proposed", proposed_plan, state.match_id, state.round_uid)
            decision_id = self.store.record("decision", {'schema_version':'decision.stream.v1' if choice else Decision.contract_version,
                                           "decision":decision.model_dump() if decision else {'q':context.q,
                                               'action_id':next(k for k,a in context.actions.items() if a.id==action.id),
                                               'reason':None,'plan_update':None},
                                           'annotation_status':'pending' if choice else 'complete',"action":action.model_dump(),
                                           "context_fingerprint":context.fingerprint, "wire_version":context.data["v"],
                                           "metrics":metrics, "shadow":self.shadow}, state.match_id, state.round_uid)
            if choice:
                tail = asyncio.create_task(self.finish_annotation(choice,context,state,decision_id,self.shadow))
                self.annotation_tasks.add(tail)
                tail.add_done_callback(self.annotation_tasks.discard)
                annotation_started = True
            if self.shadow:
                self.status = "shadow_decision_recorded"
                return
            if not self.executor:
                raise ValueError("no physical executor")
            receipt = {"id":uuid.uuid4().hex, "action":action.model_dump(), "status":"validated", "created_at":utcnow()}
            self.pending = {"receipt":receipt, "state":state, "start":start, "proposed_plan":proposed_plan}
            pending = self.pending
            async def send():
                try:
                    input_start = time.monotonic()
                    clicked = await self.executor.execute(state, action, deadline + self.settings.execution_margin)
                    receipt.update(status="mouse_sent", clicked_mono=clicked,
                                   input_ms=(clicked-input_start)*1000, read_to_mouse_ms=(clicked-start)*1000,
                                   input_steps=getattr(self.executor,'metrics',[]))
                    self.record_receipt(receipt,state.match_id,state.round_uid)
                    self.status = "awaiting_actual_event"
                    if self.pending and self.pending.get("early_ack"):
                        payload, ack_id = self.pending.pop("early_ack")
                        await self.input_observed("accepted", action.window_id, payload, ack_id)
                    if self.pending is pending and generation == self.generation:
                        self.watch_confirmation(pending)
                except Exception as error:
                    possible_press = getattr(self.executor,'press_may_have_occurred',True)
                    receipt.update(status='execution_uncertain' if possible_press else 'expired',
                                   input_steps=getattr(self.executor,'metrics',[]))
                    self.record_receipt({**receipt,
                                        'reason':type(error).__name__+(': '+str(error) if isinstance(error,(InputDeferred,TimeoutError)) else ''),
                                        'input_steps':getattr(self.executor,'metrics',[])},
                                      state.match_id, state.round_uid)
                    if possible_press:
                        self.status = 'recovering_input_confirmation'
                        self.recovery.phase = 'waiting_confirmation'
                        self.recovery_record(state,'input','waiting_actual_confirmation',error_type=type(error).__name__)
                        if self.pending is pending and generation == self.generation:
                            if pending.get('early_ack'):
                                payload,ack_id = pending.pop('early_ack')
                                await self.input_observed('accepted',action.window_id,payload,ack_id)
                            if self.pending is pending:
                                self.watch_confirmation(pending)
                    else:
                        self.pending = None
                    raise
            # A server event may arrive during mouse release. Finish the input
            # and its journal write before changed() confirms the actual event.
            await finish_input(send())
        except asyncio.CancelledError:
            self.store.record("decision_cancelled", {"window_id":op.window_id}, state.match_id, state.round_uid)
            if (generation == self.generation and self.enabled and not self.pending
                    and (state.match_id,op.window_id) not in self.confirmed_windows):
                self.failed_window(state,'window_superseded_during_decision')
            raise
        except Exception as error:
            self.store.record("decision_failure", {"window_id":op.window_id, "error_type":type(error).__name__,
                                                   "detail":str(error) if isinstance(error, (InputDeferred, TimeoutError)) else None,
                                                   "elapsed_ms":(time.monotonic()-start)*1000}, state.match_id, state.round_uid)
            if generation == self.generation:
                if self.input_stopped():
                    self.enabled, self.status = False, 'paused_input_stopped'
                    self.recovery.phase = 'stopped'
                elif isinstance(error,ModelFailure) and not error.retryable:
                    self.failed_window(state,'permanent_model_error')
                    self.enabled, self.status = False, 'paused_model_configuration'
                    self.recovery.phase = 'permanent_model_error'
                elif not self.pending and (state.match_id,op.window_id) not in self.confirmed_windows:
                    self.failed_window(state,type(error).__name__)
        finally:
            if choice and not annotation_started:
                choice.completion.cancel()
                await asyncio.gather(choice.completion,return_exceptions=True)

    async def finish_annotation(self,choice,context,state,decision_id,shadow):
        base = {'decision_record_id':decision_id,'window_id':state.operation.window_id,'q':context.q}
        try:
            decision,action,metrics = await choice.completion
            if action.id != choice.action.id:
                raise ValueError('annotation action differs from committed choice')
            self.store.record('decision_annotation',{**base,'status':'complete',
                              'decision':decision.model_dump(),'metrics':metrics},state.match_id,state.round_uid)
            update = decision.plan_update
            if shadow or update is None:
                return
            if (self.match_id != state.match_id or self.round_uid != state.round_uid
                    or (self.plan or {}).get('version',0) != update.based_on_plan_version):
                self.store.record('round_plan_late_ignored',{**base,'reason':'round_or_plan_version_changed'},
                                  state.match_id,state.round_uid)
                return
            proposed = {**update.model_dump(),'schema_version':RoundPlanUpdate.contract_version,
                        'version':update.based_on_plan_version+1,'round_uid':state.round_uid,
                        'state_version':state.state_version}
            self.store.record('round_plan_proposed',proposed,state.match_id,state.round_uid)
            if self.pending and self.pending['receipt']['action']['id'] == action.id:
                self.pending['proposed_plan'] = proposed
            elif any(r['payload'].get('status') == 'server_confirmed' and
                     r['payload']['action']['id'] == action.id for r in self.store.records('receipt',state.match_id,10000)):
                self.adopt_plan({'proposed_plan':proposed,'state':state})
        except asyncio.CancelledError:
            self.store.record('decision_annotation',{**base,'status':'cancelled','usage':None},state.match_id,state.round_uid)
            raise
        except Exception as error:
            self.store.record('decision_annotation',{**base,'status':'failed','error_type':type(error).__name__},
                              state.match_id,state.round_uid)

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
        automatic = bool(pending.get('automatic_input'))
        matches = matches and not automatic
        receipt = {**pending["receipt"], "status":"server_confirmed" if matches else "mismatch", "actual_event_id":event.id,
                   "confirmed_at":utcnow(), "elapsed_ms":(event.received_mono-pending["start"])*1000}
        if automatic:
            receipt['reason'] = 'client_automatic_input_not_bot_confirmation'
        self.record_receipt(receipt,state.match_id,pending['state'].round_uid)
        if matches:
            self.adopt_plan(pending)
            self.confirmed_window(pending['state'])
        self.bank.charge(action['window_id'],max(0,event.received_mono-pending['start']),
                         fixed_ms=pending['state'].operation.fixed_ms if self.settings.timer_verified else None)
        self.store.record("time_bank",self.bank.checkpoint(action['window_id']),state.match_id,pending['state'].round_uid)
        self.pending = None
        if not matches:
            self.enabled, self.status = False, "paused_input_mismatch"
            self.recovery.phase = 'input_mismatch'

    def adopt_plan(self, pending):
        proposed = pending.get("proposed_plan")
        if proposed:
            state = pending["state"]
            self.store.record("round_plan", proposed, state.match_id, state.round_uid)
            if self.round_uid == state.round_uid:
                self.plan = proposed
