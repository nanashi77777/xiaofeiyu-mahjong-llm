import asyncio
from types import SimpleNamespace

from majsoul_llm.browser import Browser
from majsoul_llm.config import Settings
from majsoul_llm.engine import Engine


class Emitter:
    def __init__(self):
        self.events = {}
        self.alive = True

    def on(self,name,callback):
        self.events.setdefault(name,[]).append(callback)

    def emit(self,name,*args):
        for callback in self.events.get(name,[]):
            callback(*args)

    def is_connected(self):
        return self.alive

    def is_closed(self):
        return not self.alive

    async def send(self,*args):
        pass

    async def add_init_script(self,*args):
        pass


async def attached(engine):
    b = Browser(Settings(),engine.frame)
    page,chrome,cdp = Emitter(),Emitter(),Emitter()
    b.page,b.attached_browser,b.cdp = page,chrome,cdp
    b.context = SimpleNamespace(browser=chrome)
    await b._listen()
    return b,page,chrome,cdp


async def test_page_close_immediately_invalidates_operation_and_never_invents_game_end(store,state):
    engine = Engine(store)
    engine.state,engine.connection = state,'fixture'
    b,page,chrome,cdp = await attached(engine)
    assert b.connected
    page.alive = False
    page.emit('close')
    assert not b.connected and engine.state.operation is None
    assert engine.state.lifecycle == 'interrupted'
    assert store.latest(state.match_id).lifecycle == 'interrupted'
    chrome.emit('disconnected')
    page.emit('close')
    assert len(store.records('interruption')) == 1
    assert not store.events(state.match_id)
    await b._discard_disconnected()


async def test_browser_connection_loss_is_observed_even_when_page_object_stays_open(store,state):
    engine = Engine(store)
    engine.state,engine.connection = state,'fixture'
    b,page,chrome,cdp = await attached(engine)
    chrome.alive = False
    chrome.emit('disconnected')
    assert not page.is_closed() and not b.connected
    assert engine.state.operation is None and engine.connection is None
    await b._discard_disconnected()


async def test_old_callbacks_and_queued_frames_cannot_invalidate_a_new_browser(store,state):
    engine = Engine(store)
    engine.state,engine.connection = state,'fixture'
    b,oldpage,oldchrome,oldcdp = await attached(engine)
    oldchrome.alive = False
    b.queue.put_nowait(('old',b'old',False))
    stopped = []
    async def stop_driver():
        stopped.append(True)
    b.playwright = SimpleNamespace(stop=stop_driver)
    await b._discard_disconnected()
    assert stopped == [True] and b.queue.empty() and b.consumer is None
    page,chrome,cdp = Emitter(),Emitter(),Emitter()
    b.page,b.attached_browser,b.cdp = page,chrome,cdp
    b.context = SimpleNamespace(browser=chrome)
    await b._listen()
    engine.state,engine.connection = state.model_copy(deep=True),'new'
    oldpage.emit('close')
    oldchrome.emit('disconnected')
    oldcdp.emit('Network.webSocketFrameReceived',{'requestId':'old',
        'response':{'opcode':2,'payloadData':'b2xk'}})
    assert b.connected and engine.state.operation and engine.connection == 'new'
    assert b.queue.empty()
    await b._discard_disconnected()


async def test_live_start_keeps_the_existing_window_and_capture_consumer(store,state):
    engine = Engine(store)
    b,page,chrome,cdp = await attached(engine)
    task = b.consumer
    await b.start()
    assert b.page is page and b.consumer is task and not task.done()
    await b._discard_disconnected()
    await asyncio.sleep(0)
