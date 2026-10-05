"""Tab identity, native focus, observation binding, and lease/receipt regressions.

These are protocol doubles. scripts/e2e.py also exercises the headed Docker browser.
"""

import asyncio
import json

import pytest
from pydantic import ValidationError
from test_app import FakeCoding, FakeDesktop

from desktop_bridge.app import Runtime
from desktop_bridge.backends import Browser
from desktop_bridge.models import BrowserAction
from desktop_bridge.state import BridgeError, Observation


class CDP:
    def __init__(self, page):
        self.page = page
        self.calls = []

    async def send(self, method, params=None):
        self.calls.append((method, params))
        if self.page.unreadable:
            raise RuntimeError("Target unavailable")
        if method == "Emulation.setFocusEmulationEnabled":
            assert params == {"enabled": False}
            self.page.emulated = False
            return {}
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "frame"}}}
        if method == "Page.createIsolatedWorld":
            assert params == {"frameId": "frame", "worldName": "desktop-bridge-tabs"}
            return {"executionContextId": 7}
        if method == "Runtime.evaluate":
            assert params["contextId"] == 7 and params["returnByValue"] is True
            assert not self.page.emulated
            return {"result": {"value": {
                "visible": self.page.visible, "focused": self.page.focused,
            }}}
        raise AssertionError(method)


class Locator:
    def __init__(self, page):
        self.page = page

    async def aria_snapshot(self):
        if self.page.on_snapshot:
            self.page.on_snapshot()
        return f'- heading "{self.page.name}"'

    async def count(self):
        if self.page.on_count:
            self.page.on_count()
        return self.page.matches

    async def click(self, **kwargs):
        self.page.effects.append(("click",))
        if self.page.on_click:
            self.page.on_click()

    async def fill(self, text, **kwargs):
        self.page.effects.append(("fill", text))

    async def press(self, key, **kwargs):
        self.page.effects.append(("press", key))


class Page:
    def __init__(self, context, name, *, visible=False, focused=False):
        self.context = context
        self.name = name
        self.url = "https://example.test/" + name
        self.visible, self.focused = visible, focused
        self.closed = False
        self.unreadable = False
        self.emulated = True
        self.effects = []
        self.callbacks = []
        self.matches = 1
        self.on_snapshot = self.on_count = self.on_click = None
        self.cdp = CDP(self)
        context.pages.append(self)

    def is_closed(self):
        return self.closed

    def once(self, event, callback):
        assert event == "close"
        self.callbacks.append(callback)

    async def close(self):
        self.closed = True
        self.context.pages.remove(self)
        for callback in self.callbacks:
            callback(self)

    async def title(self):
        return self.name

    async def bring_to_front(self):
        self.effects.append(("bring_to_front",))
        self.context.activate(self)

    async def goto(self, url, **kwargs):
        self.effects.append(("goto", url))
        self.url = url

    def locator(self, selector):
        assert selector == "body"
        return Locator(self)

    def get_by_role(self, role, **kwargs):
        assert role == "button"
        assert kwargs == {"name": "Test", "exact": True}
        return Locator(self)


class Context:
    def __init__(self):
        self.pages = []
        self.sessions = []

    def activate(self, page):
        for other in self.pages:
            other.visible = other.focused = other is page

    async def new_page(self):
        page = Page(self, "new")
        self.activate(page)
        return page

    async def new_cdp_session(self, page):
        self.sessions.append(page)
        return page.cdp


class Chromium:
    def __init__(self, contexts):
        self.contexts = contexts
        self.connected = True

    def is_connected(self):
        return self.connected


@pytest.fixture
def browser():
    backend = Browser()
    context = Context()
    backend.browser = Chromium([context])
    Page(context, "first", visible=True, focused=True)
    Page(context, "second")
    return backend


def observed(state):
    return Observation(0, 0, state["tab_id"], tuple(tab["id"] for tab in state["tabs"]))


async def perform(browser, action, state=None):
    state = state or await browser.tab_state()
    return await browser.perform(action, observation=observed(state), guard=lambda: None)


@pytest.mark.parametrize("kind", ["navigate", "new_tab"])
@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "https://user:pass@example.test", "", "https://"])
def test_navigation_schema_rejects_unsafe_urls(kind, url):
    with pytest.raises(ValidationError):
        BrowserAction(kind=kind, url=url)


@pytest.mark.parametrize("tab_id", ["", "first", "tab-0", "tab--1", "tab-1\n", 1, True])
def test_select_schema_requires_tab_id(tab_id):
    with pytest.raises(ValidationError):
        BrowserAction(kind="select_tab", tab_id=tab_id)


def test_browser_schema_preserves_old_actions_and_adds_explicit_tabs():
    assert BrowserAction(kind="click", role="button", name="Test").kind == "click"
    assert BrowserAction(kind="navigate", url="https://example.test").kind == "navigate"
    assert BrowserAction(kind="new_tab", url="https://example.test").kind == "new_tab"
    assert BrowserAction(kind="select_tab", tab_id="tab-12").tab_id == "tab-12"
    with pytest.raises(ValidationError):
        BrowserAction(kind="click", role="button", tab_id="tab-2")
    with pytest.raises(ValidationError):
        BrowserAction(kind="close_tab", tab_id="tab-2")


async def test_snapshot_follows_visible_popup_then_human_switch_back(browser):
    context = browser.browser.contexts[0]
    first, second = context.pages
    snapshot = await browser.snapshot()
    assert snapshot["tab_id"] == "tab-1" and snapshot["title"] == "first"
    context.activate(second)
    snapshot = await browser.snapshot()
    assert snapshot["tab_id"] == "tab-2" and snapshot["title"] == "second"
    assert 'heading "second"' in snapshot["snapshot"]
    context.activate(first)
    assert (await browser.snapshot())["tab_id"] == "tab-1"
    assert not first.effects and not second.effects


async def test_background_tabs_do_not_steal_selection_and_ids_survive_close(browser):
    context = browser.browser.contexts[0]
    await browser.tab_state()
    third = Page(context, "background")
    state = await browser.tab_state()
    assert state["tab_id"] == "tab-1" and state["tabs"][-1]["id"] == "tab-3"
    await context.pages[1].close()
    assert "tab-2" not in browser._tabs and len(browser._sessions) == 2
    fourth = Page(context, "later")
    state = await browser.tab_state()
    assert [tab["id"] for tab in state["tabs"]] == ["tab-1", "tab-3", "tab-4"]
    await perform(browser, {"kind": "select_tab", "tab_id": "tab-3"}, state)
    assert third.effects == [("bring_to_front",)]
    assert not fourth.effects
    assert (await browser.snapshot())["tab_id"] == "tab-3"


async def test_native_focus_is_read_in_isolated_world_and_emulation_reset_each_time(browser):
    await browser.tab_state()
    await browser.tab_state()
    for page in browser.browser.contexts[0].pages:
        assert [method for method, _ in page.cdp.calls] == [
            "Emulation.setFocusEmulationEnabled", "Page.getFrameTree",
            "Page.createIsolatedWorld", "Runtime.evaluate",
        ] * 2
    assert len(browser.browser.contexts[0].sessions) == 2


async def test_multiple_visible_windows_use_unique_native_focus_or_require_selection(browser):
    first, second = browser.browser.contexts[0].pages
    second.visible = True
    assert (await browser.snapshot())["tab_id"] == "tab-1"
    first.focused = False
    snapshot = await browser.snapshot()
    assert snapshot["tab_id"] is None and snapshot["selection_required"]
    assert snapshot["snapshot"] == "" and "select_tab" in snapshot["warning"]
    assert all(not tab["active"] for tab in snapshot["tabs"])
    with pytest.raises(BridgeError) as caught:
        await perform(browser, {"kind": "navigate", "url": "https://example.test"}, snapshot)
    assert caught.value.code == "STALE_OBSERVATION"
    await perform(browser, {"kind": "select_tab", "tab_id": "tab-2"}, snapshot)
    assert (await browser.snapshot())["tab_id"] == "tab-2"


async def test_unreadable_tab_and_no_focus_do_not_fall_back_to_first_or_newest(browser):
    first, second = browser.browser.contexts[0].pages
    second.unreadable = True
    assert (await browser.snapshot())["tab_id"] is None
    second.unreadable = False
    first.visible = first.focused = False
    assert (await browser.snapshot())["tab_id"] is None


async def test_empty_browser_snapshot_is_read_only_and_new_tab_recovers(browser):
    context = browser.browser.contexts[0]
    for page in list(context.pages):
        await page.close()
    snapshot = await browser.snapshot()
    assert snapshot["tabs"] == [] and not context.pages
    result = await perform(browser, {"kind": "new_tab", "url": "https://example.test/new"}, snapshot)
    assert result["tab_id"] == "tab-1" and len(context.pages) == 1


async def test_new_tab_preserves_old_page_and_navigate_uses_current_tab(browser):
    context = browser.browser.contexts[0]
    first, second = context.pages
    old_url = first.url
    result = await perform(browser, {"kind": "new_tab", "url": "https://example.test/new"})
    assert result["tab_id"] == "tab-3" and first.url == old_url
    assert not first.effects and not second.effects
    await perform(browser, {"kind": "navigate", "url": "https://example.test/next"})
    assert context.pages[-1].url.endswith("/next")
    assert first.url == old_url


async def test_new_tab_ambiguous_across_contexts_does_not_choose_one(browser):
    browser.browser.contexts.append(Context())
    browser.browser.contexts[0].pages[0].focused = False
    browser.browser.contexts[0].pages[0].visible = False
    with pytest.raises(BridgeError) as caught:
        await perform(browser, {"kind": "new_tab", "url": "https://example.test/new"})
    assert caught.value.code == "AMBIGUOUS_TAB"
    assert len(browser.browser.contexts[0].pages) == 2


async def test_unknown_closed_and_unobserved_ids_never_fall_through(browser):
    state = await browser.tab_state()
    for target in ["tab-999", "tab-2"]:
        if target == "tab-2":
            await browser.browser.contexts[0].pages[1].close()
        with pytest.raises(BridgeError) as caught:
            await perform(browser, {"kind": "select_tab", "tab_id": target}, state)
        assert caught.value.code == "TAB_NOT_FOUND"
    Page(browser.browser.contexts[0], "unobserved")
    await browser.tab_state()
    with pytest.raises(BridgeError) as caught:
        await perform(browser, {"kind": "select_tab", "tab_id": "tab-3"}, state)
    assert caught.value.code == "STALE_OBSERVATION"
    assert all(not page.effects for page in browser.browser.contexts[0].pages)


@pytest.mark.parametrize("kind", ["navigate", "click", "fill", "press"])
async def test_tab_switch_after_observation_rejects_all_page_actions(browser, kind):
    state = await browser.tab_state()
    context = browser.browser.contexts[0]
    context.activate(context.pages[1])
    with pytest.raises(BridgeError) as caught:
        await perform(browser, {"kind": kind, "url": "https://example.test/new", "role": "button", "name": "Test"}, state)
    assert caught.value.code == "STALE_OBSERVATION"
    assert all(not page.effects for page in context.pages)


async def test_switch_during_locator_resolution_rechecks_before_side_effect(browser):
    first, second = browser.browser.contexts[0].pages
    first.on_count = lambda: first.context.activate(second)
    with pytest.raises(BridgeError) as caught:
        await perform(browser, {"kind": "click", "role": "button", "name": "Test"})
    assert caught.value.code == "STALE_OBSERVATION" and not first.effects and not second.effects


async def test_popup_during_click_does_not_change_the_bound_action_target(browser):
    first, second = browser.browser.contexts[0].pages
    first.on_click = lambda: first.context.activate(second)
    result = await perform(browser, {"kind": "click", "role": "button", "name": "Test"})
    assert result["tab_id"] == "tab-1"
    assert first.effects == [("click",)] and not second.effects
    assert (await browser.snapshot())["tab_id"] == "tab-2"


async def test_focus_change_during_snapshot_does_not_issue_mixed_snapshot(browser):
    first, second = browser.browser.contexts[0].pages
    first.on_snapshot = lambda: first.context.activate(second)
    with pytest.raises(BridgeError) as caught:
        await browser.snapshot()
    assert caught.value.code == "STALE_OBSERVATION"


@pytest.fixture
def runtime(tmp_path, browser):
    runtime = Runtime(tmp_path, desktop=FakeDesktop(), browser=browser, coding=FakeCoding())
    yield runtime
    runtime.session.close()


def unpack(value):
    return json.loads(value[0].text)


async def action(runtime, payload, *, observation=None, action_id="test"):
    observation = observation or unpack(await runtime.call("browser_snapshot", {}))["observation_id"]
    return unpack(await runtime.call("browser_action", {
        "action": payload, "action_id": action_id, "observation_id": observation,
    }))


@pytest.mark.parametrize("source", ["browser_snapshot", "desktop_screenshot"])
async def test_runtime_observations_bind_both_supported_sources(runtime, source):
    await runtime.call("session_start", {})
    snapshot = unpack(await runtime.call(source, {}))
    context = runtime.browser.browser.contexts[0]
    context.activate(context.pages[1])
    with pytest.raises(BridgeError) as caught:
        await action(runtime, {"kind": "navigate", "url": "https://example.test/changed"}, observation=snapshot["observation_id"])
    assert caught.value.code == "STALE_OBSERVATION"
    assert all(not page.effects for page in context.pages)


async def test_screenshot_allows_browser_action_when_tab_is_still_current(runtime):
    await runtime.call("session_start", {})
    screenshot = unpack(await runtime.call("desktop_screenshot", {}))
    assert screenshot["tab_id"] == "tab-1"
    result = await action(runtime, {"kind": "click", "role": "button", "name": "Test"}, observation=screenshot["observation_id"])
    assert result["tab_id"] == "tab-1"


async def test_screenshot_tab_race_and_disconnect_keep_pixels_available(runtime):
    await runtime.call("session_start", {})
    context = runtime.browser.browser.contexts[0]

    async def changing_screenshot():
        context.activate(context.pages[1])
        return b"pixels"

    runtime.desktop.screenshot = changing_screenshot
    screenshot = unpack(await runtime.call("desktop_screenshot", {}))
    assert screenshot["tab_id"] is None and screenshot["selection_required"]
    with pytest.raises(BridgeError) as caught:
        await action(runtime, {"kind": "click", "role": "button", "name": "Test"}, observation=screenshot["observation_id"])
    assert caught.value.code == "STALE_OBSERVATION"
    runtime.browser.browser.connected = False
    screenshot = unpack(await runtime.call("desktop_screenshot", {}))
    assert screenshot["tab_id"] is None and "BROWSER_DISCONNECTED" in screenshot["warning"]
    result = await runtime.call("desktop_action", {
        "action": {"kind": "click"}, "action_id": "pixels", "observation_id": screenshot["observation_id"],
    })
    assert unpack(result)["ok"] is True


async def test_ambiguous_snapshot_can_select_and_receipt_replay_never_refocuses(runtime):
    await runtime.call("session_start", {})
    context = runtime.browser.browser.contexts[0]
    context.pages[0].visible = context.pages[0].focused = False
    snapshot = unpack(await runtime.call("browser_snapshot", {}))
    payload = {"kind": "select_tab", "tab_id": "tab-2"}
    result = await action(runtime, payload, observation=snapshot["observation_id"])
    assert result["tab_id"] == "tab-2"
    context.activate(context.pages[0])
    replay = await action(runtime, payload, observation=snapshot["observation_id"])
    assert replay["replayed"] is True
    assert context.pages[0].focused and context.pages[1].effects == [("bring_to_front",)]
    with pytest.raises(BridgeError) as caught:
        await action(runtime, {"kind": "select_tab", "tab_id": "tab-1"}, observation=snapshot["observation_id"])
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"


async def test_new_tab_receipt_replay_never_creates_second_tab(runtime):
    await runtime.call("session_start", {})
    snapshot = unpack(await runtime.call("browser_snapshot", {}))
    payload = {"kind": "new_tab", "url": "https://example.test/new"}
    result = await action(runtime, payload, observation=snapshot["observation_id"])
    replay = await action(runtime, payload, observation=snapshot["observation_id"])
    assert result["tab_id"] == replay["tab_id"] == "tab-3" and replay["replayed"]
    assert len(runtime.browser.browser.contexts[0].pages) == 3


@pytest.mark.parametrize("mode", ["private", "paused", "human", "stopped"])
@pytest.mark.parametrize("kind", ["select_tab", "new_tab"])
async def test_tab_management_obeys_all_control_modes(runtime, mode, kind):
    await runtime.call("session_start", {})
    snapshot = unpack(await runtime.call("browser_snapshot", {}))
    await runtime.control(mode)
    with pytest.raises(BridgeError) as caught:
        await action(runtime, {"kind": kind, **({"tab_id": "tab-2"} if kind == "select_tab" else {"url": "https://example.test"})}, observation=snapshot["observation_id"])
    assert caught.value.code == "CONTROL_NOT_OWNED"
    if mode == "private":
        for source in ["desktop_screenshot", "browser_snapshot"]:
            with pytest.raises(BridgeError) as hidden:
                await runtime.call(source, {})
            assert hidden.value.code == "PRIVATE_TAKEOVER"
    assert len(runtime.browser.browser.contexts[0].pages) == 2


async def test_takeover_during_browser_preflight_stops_input_and_holds_lease(runtime):
    await runtime.call("session_start", {})
    snapshot = unpack(await runtime.call("browser_snapshot", {}))
    started, finish = asyncio.Event(), asyncio.Event()
    original = runtime.browser.tab_state

    async def delayed_state():
        started.set()
        await finish.wait()
        return await original()

    runtime.browser.tab_state = delayed_state
    pending = asyncio.create_task(action(runtime, {"kind": "navigate", "url": "https://example.test/new"}, observation=snapshot["observation_id"]))
    await started.wait()
    control = asyncio.create_task(runtime.control("private"))
    await asyncio.sleep(0)
    assert runtime.session.mode == "private" and runtime.session.lock.locked()
    finish.set()
    with pytest.raises(BridgeError) as caught:
        await pending
    assert caught.value.code == "CONTROL_NOT_OWNED"
    await control
    assert all(not page.effects for page in runtime.browser.browser.contexts[0].pages)


@pytest.mark.parametrize("observation_id", [None, "", 1, False, []])
async def test_missing_or_nonstring_observation_cannot_bypass_freshness(runtime, observation_id):
    await runtime.call("session_start", {})
    for tool in ["browser_action", "desktop_action"]:
        with pytest.raises(BridgeError) as caught:
            await runtime.call(tool, {
                "action": {"kind": "click", **({"role": "button", "name": "Test"} if tool == "browser_action" else {})},
                "observation_id": observation_id, "action_id": "no-observation",
            })
        assert caught.value.code == "STALE_OBSERVATION"


async def test_existing_browser_receipt_fingerprint_remains_compatible(runtime):
    await runtime.call("session_start", {})
    payload = {"kind": "navigate", "url": "https://example.test", "role": "", "name": "", "text": "", "key": "Enter"}
    async with runtime.session.action("old", {"tool": "browser_action", "action": payload}) as ticket:
        ticket["result"] = {"ok": True, "url": payload["url"]}
    result = await action(runtime, payload, observation="old-observation", action_id="old")
    assert result["replayed"] is True
    assert all(not page.effects for page in runtime.browser.browser.contexts[0].pages)


async def test_page_closing_while_cdp_session_attaches_leaves_no_orphan(browser):
    context = browser.browser.contexts[0]
    first, second = context.pages
    original = context.new_cdp_session

    async def close_before_session_reply(page):
        session = await original(page)
        if page is second:
            await page.close()
        return session

    context.new_cdp_session = close_before_session_reply
    await browser.tab_state()
    assert list(browser._tabs.values()) == [first]
    assert list(browser._sessions) == [first]
