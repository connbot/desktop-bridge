"""Thin adapters over Cua, Playwright and the user's Coding Tools MCP."""

from __future__ import annotations

import asyncio
import base64
import os
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .state import BridgeError


class Desktop:
    def __init__(self):
        # Pinned upstream handler; no independent GUI driver implementation.
        from computer_server.handlers.vnc import VNCAutomationHandler, _VNCConnection

        class X11Connection(_VNCConnection):
            def scroll(self, x, y):
                # Upstream uses arrow keys for macOS VNC. X11 needs actual RFB
                # wheel events, otherwise scrolling edits focused text fields.
                def send(client):
                    for count, positive, negative in ((y, 4, 5), (x, 7, 6)):
                        for _ in range(abs(count)):
                            client.mousePress(positive if count > 0 else negative)

                return self._with_client(send)

        self.handler = VNCAutomationHandler(host="127.0.0.1", port=5900)
        self.handler._conn = X11Connection("127.0.0.1", 5900)

    async def screenshot(self):
        result = await self.handler.screenshot()
        self.check(result)
        return base64.b64decode(result["image_data"])

    @staticmethod
    def check(result):
        if not result.get("success"):
            raise BridgeError("DESKTOP_ERROR", result.get("error", "Desktop operation failed"))
        return result

    async def perform(self, action):
        h = self.handler
        kind = action["kind"]
        if kind == "click":
            method = {"left": h.left_click, "right": h.right_click, "middle": h.middle_click}
            for _ in range(action.get("count", 1)):
                self.check(await method[action.get("button", "left")](action["x"], action["y"]))
        elif kind == "move":
            self.check(await h.move_cursor(action["x"], action["y"]))
        elif kind == "scroll":
            self.check(await h.move_cursor(action["x"], action["y"]))
            # Public API uses wheel ticks: positive dy means DOWN.
            self.check(await h.scroll(action.get("dx", 0), -action.get("dy", 0)))
        elif kind == "drag":
            self.check(
                await h.drag(
                    [tuple(p) for p in action["path"]], button=action.get("button", "left")
                )
            )
        elif kind == "key":
            self.check(await h.hotkey(action["keys"]))
        elif kind == "type":
            # RFB keysyms alone cannot reliably type Chinese. Local X clipboard
            # carries UTF-8, then Cua sends paste to the SAME X11 desktop.
            proc = await asyncio.create_subprocess_exec(
                "xclip",
                "-selection",
                "clipboard",
                "-in",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(action["text"].encode()), timeout=5)
            if proc.returncode:
                raise BridgeError("CLIPBOARD_ERROR", "Unable to set UTF-8 clipboard")
            self.check(await h.hotkey(["ctrl", "v"]))
        return {"ok": True}


class Browser:
    def __init__(self, endpoint="http://127.0.0.1:9222"):
        self.endpoint = endpoint
        self.pw = None
        self.browser = None
        self._tabs = {}
        self._sessions = {}
        self._next_tab_id = 1

    async def connect(self):
        from playwright.async_api import async_playwright

        self.pw = await async_playwright().start()
        for _ in range(30):
            try:
                self.browser = await self.pw.chromium.connect_over_cdp(self.endpoint)
                return
            except Exception:
                await asyncio.sleep(1)
        raise RuntimeError("Headed Chromium CDP did not become ready")

    def _live_tabs(self):
        if not self.browser or not self.browser.is_connected():
            raise BridgeError(
                "BROWSER_DISCONNECTED", "Restart desktop service to reconnect Chromium"
            )
        pages = [
            page for context in self.browser.contexts for page in context.pages
            if not page.is_closed()
        ]
        # Include sessions whose target closed while new_cdp_session was awaiting
        # its reply, after the close callback already removed the tab entry.
        for page in set(self._tabs.values()) | self._sessions.keys():
            if page not in pages:
                self._forget(page)
        for page in pages:
            if page not in self._tabs.values():
                self._tabs[f"tab-{self._next_tab_id}"] = page
                self._next_tab_id += 1
                page.once("close", lambda closed=page: self._forget(closed))
        return list(self._tabs.items())

    def _forget(self, page):
        self._tabs = {key: value for key, value in self._tabs.items() if value is not page}
        # Closing a target automatically detaches its CDP sessions.
        self._sessions.pop(page, None)

    async def _visibility(self, page):
        session = self._sessions.get(page)
        if session is None:
            session = await page.context.new_cdp_session(page)
            self._sessions[page] = session
        # Playwright 1.62 enables this override on every attached page. Disable
        # only focus emulation, preserving download and media defaults. Reapply
        # after renderer changes or another CDP client's initialization.
        await session.send("Emulation.setFocusEmulationEnabled", {"enabled": False})
        frame = (await session.send("Page.getFrameTree"))["frameTree"]["frame"]["id"]
        world = await session.send(
            "Page.createIsolatedWorld", {"frameId": frame, "worldName": "desktop-bridge-tabs"}
        )
        # A website can overwrite document.hasFocus/visibilityState in its main
        # world. Read native properties in our isolated world, never page code.
        result = await session.send("Runtime.evaluate", {
            "expression": "({visible: document.visibilityState === 'visible', focused: document.hasFocus()})",
            "contextId": world["executionContextId"],
            "returnByValue": True,
        })
        value = result.get("result", {}).get("value")
        if (
            result.get("exceptionDetails") or not isinstance(value, dict)
            or type(value.get("visible")) is not bool
            or type(value.get("focused")) is not bool
        ):
            raise BridgeError("BROWSER_TAB_UNAVAILABLE", "Cannot read native browser visibility")
        return value

    async def tab_state(self):
        """Inventory live tabs without ever selecting one as an observation side effect."""
        live = self._live_tabs()

        async def inspect(tab_id, page):
            tab = {"id": tab_id, "title": "", "url": page.url, "active": False}
            try:
                async with asyncio.timeout(5):
                    visibility = await self._visibility(page)
                    tab["title"] = await page.title()
                return tab, visibility
            except Exception:
                # A navigating, closed, crashed, or unresponsive target must not
                # make us guess another tab is active. The next observation can retry.
                return tab, None

        inspected = await asyncio.gather(*(inspect(key, page) for key, page in live))
        tabs = [tab for tab, _ in inspected]
        visible = [tab for tab, state in inspected if state and state["visible"]]
        focused = [tab for tab, state in inspected if state and state["visible"] and state["focused"]]
        active = None
        if all(state is not None for _, state in inspected):
            if len(visible) == 1:
                active = visible[0]
            elif len(focused) == 1:
                active = focused[0]
        # Detect targets opening/closing while we inspected; do not combine an
        # old inventory with a newly focused popup or a reused positional index.
        if live != self._live_tabs():
            active = None
        if active is not None:
            active["active"] = True
        value = {"tab_id": active["id"] if active else None, "tabs": tabs}
        if active is None:
            value["selection_required"] = True
            value["warning"] = (
                "Cannot determine one active browser tab. Use select_tab with an id from tabs, "
                "or bring a browser tab to the foreground and observe again. "
                "Use new_tab with an HTTP(S) URL if no tab is open."
            )
        return value

    async def page(self, expected_tab_id):
        state = await self.tab_state()
        if expected_tab_id is None or state["tab_id"] != expected_tab_id:
            raise BridgeError(
                "STALE_OBSERVATION", "Active browser tab changed or is ambiguous; take a fresh snapshot"
            )
        return self._tabs[expected_tab_id]

    async def snapshot(self):
        state = await self.tab_state()
        if state["tab_id"] is None:
            return {**state, "url": "", "title": "", "snapshot": ""}
        page = self._tabs[state["tab_id"]]
        value = {
            "url": page.url,
            "title": await page.title(),
            "snapshot": (await page.locator("body").aria_snapshot())[:40000],
        }
        current = await self.tab_state()
        if current["tab_id"] != state["tab_id"]:
            raise BridgeError("STALE_OBSERVATION", "Active browser tab changed while observing; retry")
        return {**current, **value}

    async def perform(self, action, *, observation, guard):
        kind = action["kind"]
        if kind == "select_tab":
            self._live_tabs()
            target = action["tab_id"]
            if target not in self._tabs:
                raise BridgeError("TAB_NOT_FOUND", "Tab is closed or unknown; take a fresh snapshot")
            if target not in observation.browser_tab_ids:
                raise BridgeError("STALE_OBSERVATION", "Tab was not observed; take a fresh snapshot")
            page = self._tabs[target]
            guard()
            await page.bring_to_front()
        elif kind == "new_tab":
            self._live_tabs()
            if observation.browser_tab_id is not None:
                context = (await self.page(observation.browser_tab_id)).context
            elif len(self.browser.contexts) == 1:
                context = self.browser.contexts[0]
            else:
                raise BridgeError("AMBIGUOUS_TAB", "Select a tab before opening one in multiple contexts")
            guard()
            page = await context.new_page()
            self._live_tabs()
            guard()
            await page.bring_to_front()
            guard()
            await page.goto(action["url"], wait_until="domcontentloaded", timeout=20000)
        else:
            page = await self.page(observation.browser_tab_id)
            if kind == "navigate":
                guard()
                await page.goto(action["url"], wait_until="domcontentloaded", timeout=20000)
            else:
                locator = page.get_by_role(action["role"], name=action["name"], exact=True)
                if await locator.count() != 1:
                    raise BridgeError("AMBIGUOUS_TARGET", "Need exactly one matching role/name")
                await self.page(observation.browser_tab_id)
                guard()
                if kind == "click":
                    await locator.click(timeout=10000)
                elif kind == "fill":
                    await locator.fill(action["text"], timeout=10000)
                elif kind == "press":
                    await locator.press(action["key"], timeout=10000)
        # This is the tab acted on, even if the action opened a popup. A fresh
        # snapshot determines what is visible next; never silently retarget.
        self._live_tabs()
        return {"ok": True, "url": page.url,
                "tab_id": next((key for key, value in self._tabs.items() if value is page), None)}

    async def close(self):
        if self.pw:
            await self.pw.stop()
        self._tabs.clear()
        self._sessions.clear()


class Coding:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.permission_mode = os.environ.get("BRIDGE_CODING_PERMISSION_MODE", "safe")
        if self.permission_mode not in {"safe", "trusted", "dangerous"}:
            raise ValueError("BRIDGE_CODING_PERMISSION_MODE must be safe, trusted, or dangerous")
        self.stack = AsyncExitStack()
        self.client = None
        self.tools = []
        self.running_commands: set[str] = set()

    async def connect(self):
        import sys

        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "LANG", "DISPLAY", "PYTHONPATH"}
        }
        env["CODING_TOOLS_MCP_TELEMETRY"] = "off"
        streams = await self.stack.enter_async_context(
            stdio_client(
                StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "coding_tools_mcp", "--stdio", "--workspace", str(self.workspace),
                          "--permission-mode", self.permission_mode],
                    env=env,
                )
            )
        )
        self.client = await self.stack.enter_async_context(ClientSession(*streams))
        await self.client.initialize()
        self.tools = (await self.client.list_tools()).tools

    async def call(self, name, arguments):
        if name not in {t.name for t in self.tools}:
            raise BridgeError("UNKNOWN_TOOL", "Unknown Coding Tools operation")
        result = await self.client.call_tool(name, arguments, read_timeout_seconds=None)
        info = result.structuredContent or {}
        command_id = info.get("command_id")
        if command_id:
            if info.get("status") == "running":
                self.running_commands.add(command_id)
            else:
                self.running_commands.discard(command_id)
        return result

    async def cancel_running(self):
        failures = []
        for command_id in list(self.running_commands):
            try:
                result = await self.call(
                    "kill_command",
                    {
                        "command_id": command_id,
                        "signal": "TERM",
                        "wait_ms": 1000,
                        "kill_wait_ms": 1000,
                    },
                )
                if result.isError:
                    failures.append(command_id)
                else:
                    self.running_commands.discard(command_id)
            except Exception:
                failures.append(command_id)
        return failures

    async def close(self):
        await self.stack.aclose()
