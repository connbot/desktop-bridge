#!/usr/bin/env python3
"""Deterministic personal-context acceptance against the local CI Docker service.

Uses fictional data, real MCP transport, real Coding Tools MCP, and the real UI.
This exercises continuity and tool execution, not autonomous model success.
Run only against the disposable test desktop used by scripts/e2e.py.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

import httpx
from capture_personal_demo import authenticate, unpack
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from playwright.async_api import async_playwright, expect

URL = "http://127.0.0.1:8080"
OUT = Path("artifacts/personal-context")
TASK_ID = "fictional-creative-weekend"
ARTIFACT = "personal-demo/weekend-notes.md"
UI_TASK_TITLE = "Review the fictional Saturday draft"
UI_PREFERENCES = "Quiet places, walkable plans, editable files. UI edit verified with fictional data."



async def run_disabled():
    """Prove optional memory is off while the actual desktop/file tools remain usable."""
    await asyncio.to_thread(OUT.mkdir, parents=True, exist_ok=True)
    async with httpx.AsyncClient(base_url=URL, timeout=60) as http:
        csrf, token = await authenticate(http, URL)
        owner = {"X-CSRF-Token": csrf, "Origin": URL}
        status = (await http.get("/api/status")).json()
        assert status["ready"] is True
        assert status["context_store"] == {
            "provider": "disabled", "configured": False, "healthy": None,
            "error": "CONTEXT_DISABLED",
        }, status["context_store"]
        for method, path, body in [
            ("GET", "/api/personal", None),
            ("PUT", "/api/personal/profile", {
                "expected_revision": 0, "profile": {"name": "Rejected fictional profile"},
            }),
        ]:
            response = await http.request(method, path, json=body, headers=owner)
            assert response.status_code == 400, response.text
            assert response.json()["error"] == "context_disabled", response.text
        async with streamablehttp_client(
            URL + "/mcp", headers={"Authorization": "Bearer " + token}
        ) as streams:
            async with ClientSession(streams[0], streams[1]) as client:
                await client.initialize()
                started = await client.call_tool("session_start", {})
                assert not started.isError
                denied = await client.call_tool("personal_context", {})
                assert denied.isError and "CONTEXT_DISABLED" in str(denied)
                denied = await client.call_tool("personal_update_context", {
                    "expected_revision": 0, "action_id": str(uuid.uuid4()),
                    "profile": {"name": "Rejected fictional profile"},
                })
                assert denied.isError and "CONTEXT_DISABLED" in str(denied)
                result = await client.call_tool("coding_apply_patch", {
                    "bridge_action_id": str(uuid.uuid4()),
                    "patch": "*** Begin Patch\n*** Add File: memory-off-check.txt\n"
                             "+Fictional test: Coding Tools works with optional memory off.\n*** End Patch",
                })
                assert not result.isError, result
                result = await client.call_tool("coding_read_file", {
                    "path": "memory-off-check.txt", "bridge_action_id": str(uuid.uuid4()),
                })
                assert not result.isError and "optional memory off" in str(result)
                listed = await client.call_tool("artifacts_list", {})
                assert not listed.isError
                assert not any(item["path"].startswith("personal/")
                               for item in unpack(listed)["files"])
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            ui = await browser.new_context(viewport={"width": 1600, "height": 1000})
            await ui.add_cookies([{"name": c.name, "value": c.value, "url": URL}
                                  for c in http.cookies.jar])
            page = await ui.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(URL)
            await expect(page.locator("#context-provider")).to_have_text("Optional memory · off")
            for selector in ("#edit-profile", "#new-task", "#import-context"):
                await expect(page.locator(selector)).to_be_disabled()
            await expect(page.locator("#export-context")).to_have_attribute("aria-disabled", "true")
            await expect(page.locator("#personal-task-list")).to_contain_text(
                "Your desktop and Coding Tools MCP work without it.")
            await page.locator('#screen[data-connected="true"]').wait_for(timeout=30000)
            await page.locator(".personal-workspace").screenshot(path=str(OUT / "context-off.png"))
            assert not errors, errors
            await browser.close()
    (OUT / "disabled.json").write_text(json.dumps({
        "complete": True, "fictional_data": True,
        "verified": ["Memory is disabled by default without provider configuration",
                     "HTTP and MCP context writes are rejected without storing the sample profile",
                     "Real Coding Tools creates and reads a file with optional memory off",
                     "The real viewer connects and UI clearly labels optional memory as off"],
    }, indent=2))


async def run(restart=False):
    await asyncio.to_thread(OUT.mkdir, parents=True, exist_ok=True)
    verified = []
    async with httpx.AsyncClient(base_url=URL, timeout=60) as http:
        csrf, token = await authenticate(http, URL)
        headers = {"Authorization": "Bearer " + token}
        owner = {"X-CSRF-Token": csrf, "Origin": URL}
        async with streamablehttp_client(URL + "/mcp", headers=headers) as streams:
            async with ClientSession(streams[0], streams[1]) as client:
                await client.initialize()

                async def call(name, args=None):
                    result = await client.call_tool(name, args or {})
                    assert not result.isError, (name, result)
                    return unpack(result)

                context = await call("personal_context")
                if restart:
                    task = next(task for task in context["tasks"] if task["id"] == TASK_ID)
                    assert context["profile"]["name"] == "Alex · fictional demo"
                    assert task["status"] == "completed" and task["artifact_details"][0]["available"]
                    expected = json.loads((OUT / "expected-context.json").read_text())
                    assert context == expected, (context, expected)
                    assert context["profile"]["preferences"] == UI_PREFERENCES
                    assert len(context["tasks"]) == 2
                    (OUT / "restart.json").write_text(json.dumps({
                        "verified": True, "revision": context["revision"],
                        "checks": ["Exact saved context survived restart", "Both artifacts remain available"],
                    }, indent=2))
                    return
                await http.post("/api/control/agent", headers=owner)
                await call("personal_update_context", {
                    "expected_revision": context["revision"], "action_id": str(uuid.uuid4()),
                    "profile": {
                        "name": "Alex · fictional demo",
                        "preferences": "Quiet places, walkable plans, editable files. Explain the trade-offs.",
                        "goals": "Make room for creative weekends. Build small tools that save time.",
                        "constraints": "Fictional demo budget: $80 for a day out. Keep afternoons flexible.",
                    },
                })
                context = await call("personal_context")
                await call("personal_record_task", {
                    "expected_revision": context["revision"], "action_id": str(uuid.uuid4()),
                    "task": {"id": TASK_ID, "title": "Plan a quiet, creative Saturday", "status": "in_progress",
                             "next_step": "Create an editable plan using saved preferences."},
                })
                # Preserve the user's own execution engine rather than writing from the test host.
                await call("coding_apply_patch", {
                    "bridge_action_id": str(uuid.uuid4()),
                    "patch": f"*** Begin Patch\n*** Add File: {ARTIFACT}\n"
                             "+# Fictional demo: a creative Saturday\n"
                             "+\n+Preferences: quiet places, walking, editable files.\n"
                             "+Morning: a sketchbook walk. Afternoon: a flexible cafe break.\n"
                             "+Before choosing venues, ask for a city and date and verify hours and prices.\n"
                             "+This is a draft plan. Nothing has been booked or purchased.\n*** End Patch",
                })
                readback = await client.call_tool("coding_read_file", {
                    "path": ARTIFACT, "bridge_action_id": str(uuid.uuid4()),
                })
                assert not readback.isError and "Nothing has been booked" in str(readback)
                context = await call("personal_context")
                await call("personal_record_task", {
                    "expected_revision": context["revision"], "action_id": str(uuid.uuid4()),
                    "task": {"id": TASK_ID, "title": "Plan a quiet, creative Saturday", "status": "completed",
                             "summary": "Created an editable draft shaped around the fictional profile.",
                             "next_step": "Provide a city and date to check real venues and costs.",
                             "evidence": "Coding Tools MCP created the Markdown plan and read it back. No booking or purchase was made.",
                             "artifacts": [ARTIFACT]},
                })
                verified.append("Preferences → task → real Coding Tools artifact → evidenced result")
                context = await call("personal_context")
                await http.post("/api/control/private", headers=owner)
                denied = await client.call_tool("personal_context", {})
                assert denied.isError and "PRIVATE_TAKEOVER" in str(denied)
                denied = await client.call_tool("personal_update_context", {
                    "expected_revision": context["revision"], "action_id": str(uuid.uuid4()), "profile": {},
                })
                assert denied.isError and "CONTROL_NOT_OWNED" in str(denied)
                await http.post("/api/control/agent", headers=owner)
                verified.append("Private mode blocks context reads and profile writes")
        # A different MCP client instance sees the same revision and results.
        async with streamablehttp_client(URL + "/mcp", headers=headers) as streams:
            async with ClientSession(streams[0], streams[1]) as next_client:
                await next_client.initialize()
                result = await next_client.call_tool("personal_context", {})
                again = unpack(result)
                assert again["revision"] == context["revision"]
                assert again["profile"]["name"] == "Alex · fictional demo"
                assert again["tasks"][0]["artifact_details"][0]["available"]
                verified.append("A fresh MCP client reuses the exact saved context revision and result")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            ui = await browser.new_context(viewport={"width": 1600, "height": 1000})
            await ui.add_cookies([{"name": c.name, "value": c.value, "url": URL} for c in http.cookies.jar])
            page = await ui.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(URL)
            await expect(page.locator("#context-provider")).to_have_text("Local file · connected")
            await expect(page.locator("#profile-summary")).to_have_text("For Alex · fictional demo")
            await page.locator("#personal-task-list").get_by_role(
                "link", name="↓ " + ARTIFACT, exact=True).wait_for()

            # Save a real owner preference edit through the visible form.
            await page.locator("#edit-profile").click()
            await page.locator("#profile-preferences").fill(UI_PREFERENCES)
            await page.get_by_role("button", name="Save preferences", exact=True).click()
            await page.locator("#profile-dialog").wait_for(state="hidden")
            saved_profile = (await http.get("/api/personal")).json()
            assert saved_profile["profile"]["preferences"] == UI_PREFERENCES
            assert saved_profile["revision"] == again["revision"] + 1
            await expect(page.locator("#profile-details")).to_contain_text(UI_PREFERENCES)
            verified.append("Visible profile form saves an owner-authored preference edit")

            # Cancel, Escape and Close discard dirty edits, including repeated opening.
            for dismissal in ("Cancel", "Escape", "Close preferences"):
                await page.locator("#edit-profile").click()
                await expect(page.locator("#profile-name")).to_have_value("Alex · fictional demo")
                await page.locator("#profile-name").fill("Unsaved fictional edit")
                if dismissal == "Escape":
                    await page.locator("#profile-name").press("Escape")
                else:
                    await page.locator("#profile-dialog").get_by_role(
                        "button", name=dismissal, exact=True).click()
                await page.locator("#profile-dialog").wait_for(state="hidden")
                assert (await http.get("/api/personal")).json() == saved_profile
            verified.append("Profile Cancel, Escape and Close preserve the exact saved revision")

            # Completion must be supported by evidence or a real existing artifact.
            await page.locator("#new-task").click()
            await page.locator("#personal-task-title").fill(UI_TASK_TITLE)
            await page.locator("#personal-task-status").select_option("completed")
            await page.get_by_role("button", name="Save task", exact=True).click()
            await expect(page.locator("#personal-task-error")).to_contain_text("Add evidence")
            assert (await http.get("/api/personal")).json() == saved_profile
            await page.locator("#personal-task-summary").fill(
                "Reviewed the editable sample plan created through Coding Tools MCP.")
            await page.locator("#personal-task-next-step").fill(
                "Ask for a city and date before checking real venues.")
            await page.locator("#personal-task-evidence").fill(
                "The real Coding Tools MCP created and read back the fictional Markdown plan. "
                "No purchase, booking, or outgoing message occurred.")
            await page.locator("#personal-task-artifacts").fill(ARTIFACT)
            # Two immediate DOM clicks exercise the actual submit guard, not an API mock.
            writes = []
            page.on("request", lambda request: writes.append(request.url)
                    if request.method == "POST" and request.url.endswith("/api/personal/tasks") else None)
            await page.get_by_role("button", name="Save task", exact=True).evaluate(
                "button => { button.click(); button.click(); }")
            await page.locator("#task-dialog").wait_for(state="hidden")
            saved_tasks = (await http.get("/api/personal")).json()
            assert len(writes) == 1, writes
            assert saved_tasks["revision"] == saved_profile["revision"] + 1
            assert len(saved_tasks["tasks"]) == 2
            ui_task = next(task for task in saved_tasks["tasks"] if task["title"] == UI_TASK_TITLE)
            assert ui_task["updated_by"] == "owner" and ui_task["status"] == "completed"
            assert ui_task["artifact_details"][0]["available"]
            card = page.locator(".personal-task").filter(has=page.get_by_role(
                "heading", name=UI_TASK_TITLE, exact=True))
            await card.get_by_role("link", name="↓ " + ARTIFACT, exact=True).wait_for()
            await card.get_by_text("Evidence / checks", exact=True).click()
            await expect(card).to_contain_text("No purchase, booking, or outgoing message occurred.")
            verified.append("UI rejects unsupported completion, then saves one evidenced task despite repeated Save clicks")

            # Re-saving an existing task updates it rather than adding another record.
            await card.get_by_role("button", name="Edit", exact=True).click()
            await expect(page.locator("#personal-task-title")).to_have_value(UI_TASK_TITLE)
            await page.get_by_role("button", name="Save task", exact=True).click()
            await page.locator("#task-dialog").wait_for(state="hidden")
            saved_tasks = (await http.get("/api/personal")).json()
            assert len(saved_tasks["tasks"]) == 2
            assert sum(task["id"] == ui_task["id"] for task in saved_tasks["tasks"]) == 1
            for dismissal in ("Cancel", "Escape", "Close task"):
                await page.locator("#new-task").click()
                await expect(page.locator("#personal-task-title")).to_have_value("")
                await page.locator("#personal-task-title").fill("Discarded fictional task")
                if dismissal == "Escape":
                    await page.locator("#personal-task-title").press("Escape")
                else:
                    await page.locator("#task-dialog").get_by_role(
                        "button", name=dismissal, exact=True).click()
                await page.locator("#task-dialog").wait_for(state="hidden")
                assert (await http.get("/api/personal")).json() == saved_tasks
            verified.append("Repeated task edit preserves its ID; Cancel, Escape and Close create no records")
            await page.locator(".personal-workspace").screenshot(path=str(OUT / "context-desktop.png"))
            await page.screenshot(path=str(OUT / "workspace-desktop.png"), full_page=True)
            async with page.expect_download() as download_info:
                await page.locator("#export-context").click()
            download = await download_info.value
            export_path = OUT / "fictional-context.json"
            await download.save_as(export_path)
            exported = json.loads(export_path.read_text())
            assert exported["revision"] == saved_tasks["revision"]
            assert len(exported["tasks"]) == 2
            assert all(task["artifacts"] == [ARTIFACT] for task in exported["tasks"])
            assert all("artifact_details" not in task for task in exported["tasks"])
            assert exported["profile"]["preferences"] == UI_PREFERENCES
            await page.locator("#context-file").set_input_files(export_path)
            await page.locator("#import-dialog").get_by_role("button", name="Cancel", exact=True).click()
            assert (await http.get("/api/personal")).json()["revision"] == exported["revision"]
            await page.locator("#context-file").set_input_files(export_path)
            await page.get_by_role("button", name="Replace context", exact=True).click()
            await page.locator("#import-dialog").wait_for(state="hidden")
            assert (await http.get("/api/personal")).json()["revision"] == exported["revision"] + 1
            final_context = (await http.get("/api/personal")).json()
            assert final_context["profile"] == exported["profile"]
            assert {task["id"] for task in final_context["tasks"]} == {task["id"] for task in exported["tasks"]}
            assert all(task["artifact_details"][0]["available"] for task in final_context["tasks"])
            (OUT / "expected-context.json").write_text(json.dumps(final_context, indent=2))
            await page.reload()
            await expect(page.locator("#profile-summary")).to_have_text("For Alex · fictional demo")
            await expect(page.locator("#task-count")).to_have_text("2")
            await page.set_viewport_size({"width": 390, "height": 844})
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            await page.locator(".personal-workspace").screenshot(path=str(OUT / "context-mobile.png"))
            assert not errors, errors
            verified.append("Context export/import round trip, cancelled import, page reload, and 390px layout preserve both real artifact references")
            await browser.close()
        async with streamablehttp_client(URL + "/mcp", headers=headers) as streams:
            async with ClientSession(streams[0], streams[1]) as final_client:
                await final_client.initialize()
                result = await final_client.call_tool("personal_context", {})
                assert not result.isError and unpack(result) == final_context
        verified.append("A new MCP client reads exactly the owner-edited and imported UI context")
        await http.post("/api/control/paused", headers=owner)
    (OUT / "evidence.json").write_text(json.dumps({"complete": True, "fictional_data": True, "verified": verified}, indent=2))
    print(json.dumps(verified, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--restart-check", action="store_true")
    group.add_argument("--disabled-check", action="store_true")
    args = parser.parse_args()
    asyncio.run(run_disabled() if args.disabled_check else run(args.restart_check))
