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
        from computer_server.handlers.vnc import VNCAutomationHandler

        self.handler = VNCAutomationHandler(host="127.0.0.1", port=5900)

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

    async def page(self):
        if not self.browser or not self.browser.is_connected():
            raise BridgeError(
                "BROWSER_DISCONNECTED", "Restart desktop service to reconnect Chromium"
            )
        context = self.browser.contexts[0]
        return context.pages[0] if context.pages else await context.new_page()

    async def snapshot(self):
        page = await self.page()
        return {
            "url": page.url,
            "title": await page.title(),
            "snapshot": (await page.locator("body").aria_snapshot())[:40000],
        }

    async def perform(self, action):
        page = await self.page()
        kind = action["kind"]
        if kind == "navigate":
            await page.goto(action["url"], wait_until="domcontentloaded", timeout=20000)
        else:
            locator = page.get_by_role(action["role"], name=action["name"], exact=True)
            if await locator.count() != 1:
                raise BridgeError("AMBIGUOUS_TARGET", "Need exactly one matching role/name")
            if kind == "click":
                await locator.click(timeout=10000)
            elif kind == "fill":
                await locator.fill(action["text"], timeout=10000)
            elif kind == "press":
                await locator.press(action["key"], timeout=10000)
        return {"ok": True, "url": page.url}

    async def close(self):
        if self.pw:
            await self.pw.stop()


class Coding:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.stack = AsyncExitStack()
        self.client = None
        self.tools = []

    async def connect(self):
        import sys

        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "LANG", "DISPLAY", "PYTHONPATH"}
        }
        streams = await self.stack.enter_async_context(
            stdio_client(
                StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "coding_tools_mcp", "--stdio", "--workspace", str(self.workspace)],
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
        return await self.client.call_tool(name, arguments, read_timeout_seconds=None)

    async def close(self):
        await self.stack.aclose()
