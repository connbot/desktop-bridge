"""Probe lifecycle regression without a real desktop or browser.

Execute the actual nested acceptance helper against a fake page and gated,
read-only fixture observer. The real transport remains covered by Docker CI.
"""

import ast
import asyncio
import json
import textwrap
from pathlib import Path

import pytest


class Page:
    def __init__(self):
        self.open = False
        self.sent = 0
        self.closed = 0
        self.source = ""

    async def evaluate(self, source, channel=None):
        if channel is not None:
            self.source = source
            assert source.count("r.sendKey(") == 1
            self.open = True
            self.sent += 1
        elif "delete window.__acceptanceInputProbe" in source:
            self.open = False
            self.closed += 1
        elif "mode:document" in source:
            return {"mode": "HUMAN", "connected": "true", "probe": {"sends": self.sent}}
        else:
            return {"sends": self.sent, "connected": self.open}

    async def screenshot(self, path, **kwargs):
        await asyncio.to_thread(Path(path).write_bytes, b"fictional-screenshot-fixture")


def helper(page, observer, tmp_path):
    source = Path(__file__).parents[1] / "scripts/e2e.py"
    tree = ast.parse(source.read_text())
    definition = next(
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "inject"
    )
    module = ast.fix_missing_locations(ast.Module(body=[definition], type_ignores=[]))

    async def observe():
        return "fixture-observation", b"fictional-desktop-fixture"

    scope = {
        "page": page,
        "headed_fixture": observer,
        "observe": observe,
        "OUT": tmp_path,
        "URL": "http://127.0.0.1:8080",
        "json": json,
    }
    exec(compile(module, str(source), "exec"), scope)
    return scope["inject"]


def assert_valid_async_script(script):
    ast.parse("async def fixture():\n" + textwrap.indent(textwrap.dedent(script), "    "))
    assert ".fill(" not in script and "sendKey(" not in script


@pytest.mark.parametrize("channel", ["view", "control"])
async def test_probe_stays_open_until_read_only_gate_completes(channel, tmp_path):
    page = Page()
    entered, release = asyncio.Event(), asyncio.Event()

    async def observer(script):
        assert_valid_async_script(script)
        if channel == "view":
            assert "actual == 'VIEW_ONLY'" in script
            assert "while time.monotonic() < until" in script
        else:
            assert "to_have_value('VIEW_ONLYz', timeout=10000)" in script
        entered.set()
        await release.wait()

    task = asyncio.create_task(helper(page, observer, tmp_path)(channel))
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.sleep(0.35)  # Longer than the old unconditional disconnect delay.
    assert page.open and page.sent == 1 and page.closed == 0
    release.set()
    await task
    assert not page.open and page.sent == 1 and page.closed == 1


async def test_failed_probe_captures_fixture_diagnostics_and_always_closes(tmp_path):
    page = Page()
    calls = 0

    async def observer(script):
        nonlocal calls
        calls += 1
        assert_valid_async_script(script)
        if calls == 1:
            raise AssertionError("Fixture input did not arrive")
        return {"value": "VIEW_ONLY", "focused": True, "activeId": "note"}

    with pytest.raises(AssertionError, match="Fixture input did not arrive"):
        await helper(page, observer, tmp_path)("control")
    assert not page.open and page.sent == 1 and page.closed == 1
    diagnostic = json.loads((tmp_path / "input-control-diagnostic.json").read_text())
    assert diagnostic["desktop"]["value"] == "VIEW_ONLY"
    assert set(diagnostic) == {"channel", "error_class", "viewer", "desktop"}
    assert (tmp_path / "input-control-viewer.png").exists()
    assert (tmp_path / "input-control-desktop.png").exists()
