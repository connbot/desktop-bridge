#!/usr/bin/env python3
"""Actual Docker acceptance: no mocked desktop, browser, MCP, or Coding Tools.

The model is deliberately not part of this deterministic integration test.
"""

import argparse
import asyncio
import base64
import hashlib
import io
import json
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from PIL import Image
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8080"
OWNER = "test-only-owner-token-not-for-deployment-123"
OUT = Path("artifacts")
OUT.mkdir(exist_ok=True)


def unpack(result):
    texts = [c.text for c in result.content if c.type == "text"]
    try:
        return json.loads(texts[0])
    except (IndexError, ValueError):
        return {"text": "\n".join(texts)}


async def authenticate(http):
    response = await http.post("/api/login", json={"token": OWNER})
    response.raise_for_status()
    csrf = response.json()["csrf"]
    client = (
        await http.post(
            "/register",
            json={
                "client_name": "CI acceptance client",
                "redirect_uris": ["http://127.0.0.1:43111/callback"],
            },
        )
    ).json()
    verifier = "test-only-verifier-" + "x" * 48
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    params = {
        "client_id": client["client_id"],
        "redirect_uri": client["redirect_uris"][0],
        "response_type": "code",
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "resource": URL + "/mcp",
        "state": "e2e",
    }
    response = await http.post("/authorize", data={**params, "csrf": csrf})
    assert response.status_code == 303, response.text
    code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
    response = await http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client["client_id"],
            "redirect_uri": params["redirect_uri"],
            "code": code,
            "code_verifier": verifier,
            "resource": URL + "/mcp",
        },
    )
    response.raise_for_status()
    return csrf, response.json()["access_token"]


async def run(restart):
    results = []
    async with httpx.AsyncClient(base_url=URL, timeout=60) as http:
        assert (await http.get("/api/status")).status_code == 401
        csrf, token = await authenticate(http)
        headers = {"X-CSRF-Token": csrf, "Origin": URL}
        async with streamablehttp_client(
            URL + "/mcp", headers={"Authorization": "Bearer " + token}
        ) as streams:
            async with ClientSession(streams[0], streams[1]) as client:
                await client.initialize()

                async def call(name, args=None, allow_error=False):
                    value = await client.call_tool(name, args or {})
                    if not allow_error:
                        assert not value.isError, (name, value)
                    return value

                async def observe():
                    result = await call("desktop_screenshot")
                    data = unpack(result)
                    image = next(c for c in result.content if c.type == "image")
                    png = base64.b64decode(image.data)
                    assert Image.open(io.BytesIO(png)).size == (1280, 800)
                    return data["observation_id"], png

                async def gui(action):
                    obs, _ = await observe()
                    return await call(
                        "desktop_action",
                        {"action": action, "observation_id": obs, "action_id": str(uuid.uuid4())},
                    )

                async def browser(action):
                    obs = unpack(await call("browser_snapshot"))["observation_id"]
                    return await call(
                        "browser_action",
                        {"action": action, "observation_id": obs, "action_id": str(uuid.uuid4())},
                    )

                tools = (await client.list_tools()).tools
                assert {
                    "desktop_action",
                    "browser_action",
                    "coding_apply_patch",
                    "coding_exec_command",
                } <= {t.name for t in tools}
                await call("session_start")
                if restart:
                    files = (await http.get("/api/artifacts")).json()["files"]
                    assert any(f["path"] == "acceptance.txt" for f in files)
                    value = await call(
                        "coding_apply_patch",
                        {
                            "patch": "*** Begin Patch\n*** Add File: acceptance.txt\n+hello desktop bridge\n*** End Patch",
                            "bridge_action_id": "persist-create",
                        },
                    )
                    assert not value.isError
                    results.append(
                        "Restart retained workspace and durable successful action receipt"
                    )
                else:
                    await browser({"kind": "navigate", "url": URL + "/static/demo.html"})
                    assert "acceptance lab" in unpack(await call("browser_snapshot"))["title"]
                    results.append("Structured Playwright operates visible headed Chromium")
                    await browser({"kind": "click", "role": "textbox", "name": "Project note"})
                    await gui({"kind": "type", "text": "你好，Hackathon!\nUTF-8 ✓"})
                    await browser({"kind": "click", "role": "button", "name": "Save note"})
                    snap = unpack(await call("browser_snapshot"))
                    assert "你好，Hackathon!" in snap["snapshot"], snap
                    results.append("Real Cua keyboard paste handles Chinese, newline and symbols")
                    obs, png = await observe()
                    (OUT / "desktop.png").write_bytes(png)
                    action = {
                        "action": {"kind": "move", "x": 100, "y": 150},
                        "observation_id": obs,
                        "action_id": "pixel-move",
                    }
                    await call("desktop_action", action)
                    assert unpack(await call("desktop_action", action))["replayed"] is True
                    stale = await call("desktop_action", {**action, "action_id": "stale"}, True)
                    assert stale.isError and "STALE_OBSERVATION" in str(stale)
                    results.append("Pixel action, receipt replay and stale observation rejection")
                    await call(
                        "coding_apply_patch",
                        {
                            "patch": "*** Begin Patch\n*** Add File: acceptance.txt\n+hello desktop bridge\n*** End Patch",
                            "bridge_action_id": "persist-create",
                        },
                    )
                    read = await call(
                        "coding_read_file",
                        {"path": "acceptance.txt", "bridge_action_id": "read-created"},
                    )
                    assert "hello desktop bridge" in str(read)
                    command = await call(
                        "coding_exec_command",
                        {
                            "cmd": "wc -c acceptance.txt",
                            "yield_time_ms": 1000,
                            "bridge_action_id": "exec-wc",
                        },
                    )
                    assert "acceptance.txt" in str(command), command
                    assert (
                        await http.get("/api/artifacts/acceptance.txt")
                    ).text == "hello desktop bridge\n"
                    results.append(
                        "Coding Tools writes, reads, executes and exports real workspace file"
                    )
                    for i in range(4):
                        missing = await call(
                            "coding_read_file",
                            {"path": "does-not-exist", "bridge_action_id": f"missing-{i}"},
                            True,
                        )
                        assert missing.isError
                        assert "TOOL_RATE_LIMITED" not in str(missing)
                    results.append("Repeated legitimate attempts are not blocked by failure count")

                    running = await call(
                        "coding_exec_command",
                        {
                            "cmd": "sleep 30",
                            "yield_time_ms": 0,
                            "bridge_action_id": "managed-sleep",
                        },
                    )
                    assert running.structuredContent["status"] == "running"
                    await http.post("/api/control/human", headers=headers)
                    denied = await call(
                        "coding_exec_command",
                        {"cmd": "touch forbidden", "bridge_action_id": "denied"},
                        True,
                    )
                    assert denied.isError and "CONTROL_NOT_OWNED" in str(denied)
                    denied = await call("session_start", allow_error=True)
                    assert denied.isError
                    await http.post("/api/control/private", headers=headers)
                    for name in ["desktop_screenshot", "browser_snapshot", "artifacts_list"]:
                        assert (await call(name, allow_error=True)).isError
                    await http.post("/api/control/agent", headers=headers)
                    polled = await call(
                        "coding_write_stdin",
                        {
                            "command_id": running.structuredContent["command_id"],
                            "chars": "",
                            "yield_time_ms": 0,
                            "bridge_action_id": "poll-canceled",
                        },
                    )
                    assert polled.structuredContent["status"] != "running"
                    results.append("Managed asynchronous shell process canceled during takeover")
                    results.append(
                        "Human takeover blocks AI writes; private mode blocks model observations"
                    )

                    # The actual user-facing noVNC UI, including interrupted/repeated controls.
                    async def oauth_callback(reader, writer):
                        await reader.readuntil(b"\r\n\r\n")
                        payload = b"Authorization returned"
                        writer.write(
                            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
                            + str(len(payload)).encode()
                            + b"\r\nConnection: close\r\n\r\n"
                            + payload
                        )
                        await writer.drain()
                        writer.close()
                        await writer.wait_closed()

                    callback_server = await asyncio.start_server(oauth_callback, "127.0.0.1", 43111)
                    async with callback_server, async_playwright() as pw:
                        browser_ui = await pw.chromium.launch()
                        page = await browser_ui.new_page(viewport={"width": 1440, "height": 1000})
                        errors = []
                        page.on("pageerror", lambda e: errors.append(str(e)))
                        # Exercise the actual browser approval journey, starting logged out.
                        ui_client = (
                            await http.post(
                                "/register",
                                json={
                                    "client_name": "Browser onboarding test",
                                    "redirect_uris": ["http://127.0.0.1:43111/callback"],
                                },
                            )
                        ).json()
                        ui_verifier = "u" * 64
                        ui_challenge = (
                            base64.urlsafe_b64encode(hashlib.sha256(ui_verifier.encode()).digest())
                            .decode()
                            .rstrip("=")
                        )
                        ui_params = {
                            "client_id": ui_client["client_id"],
                            "redirect_uri": ui_client["redirect_uris"][0],
                            "response_type": "code",
                            "code_challenge_method": "S256",
                            "code_challenge": ui_challenge,
                        }
                        await page.goto(URL + "/authorize?" + urlencode(ui_params))
                        await page.screenshot(path=str(OUT / "login.png"), full_page=True)
                        await page.get_by_label("Owner access token").fill(OWNER)
                        await page.get_by_role("button", name="Open workspace").click()
                        await page.get_by_role("button", name="Approve for one hour").click()
                        await page.wait_for_url("http://127.0.0.1:43111/callback**")
                        ui_code = parse_qs(urlsplit(page.url).query)["code"][0]
                        ui_grant = await http.post(
                            "/token",
                            data={
                                "grant_type": "authorization_code",
                                "client_id": ui_client["client_id"],
                                "redirect_uri": ui_params["redirect_uri"],
                                "code": ui_code,
                                "code_verifier": ui_verifier,
                            },
                        )
                        assert ui_grant.status_code == 200, ui_grant.text
                        results.append(
                            "Browser OAuth onboarding: login, explicit approval and bound token exchange"
                        )
                        await page.goto(URL)
                        await page.locator("#screen canvas").wait_for(timeout=20000)
                        await page.get_by_role("button", name="Take control", exact=True).click()
                        await page.locator("#status").filter(has_text="HUMAN").wait_for()
                        await page.get_by_role("button", name="Hand back to AI").click()
                        await page.locator("#status").filter(has_text="AGENT").wait_for()
                        await page.get_by_role("button", name="Pause AI").click()
                        await page.locator("#status").filter(has_text="PAUSED").wait_for()
                        await page.get_by_role("button", name="Hand back to AI").click()
                        await page.locator("#status").filter(has_text="AGENT").wait_for()
                        await page.get_by_role("button", name="Refresh", exact=True).click()
                        await page.get_by_role("link", name="acceptance.txt").wait_for()
                        # Canvas existence alone is not proof of a working screen.
                        await page.wait_for_function(
                            """() => {
                          const c = document.querySelector('#screen canvas');
                          if (!c || c.width !== 1280 || c.height !== 800) return false;
                          const p = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                          const colors = new Set();
                          for (let i=0;i<p.length;i+=400) colors.add(`${p[i]},${p[i+1]},${p[i+2]}`);
                          return colors.size > 30;
                        }""",
                            timeout=20000,
                        )
                        # Deliberately remove the CLIENT viewOnly flag: the server
                        # must still reject input on the read-only VNC listener.
                        await browser(
                            {
                                "kind": "fill",
                                "role": "textbox",
                                "name": "Project note",
                                "text": "VIEW_ONLY",
                            }
                        )
                        await browser({"kind": "click", "role": "textbox", "name": "Project note"})

                        async def inject(channel):
                            await page.evaluate(
                                """async (channel) => {
                              const {default:RFB}=await import('/novnc/core/rfb.js');
                              const node=document.createElement('div');document.body.append(node);
                              await new Promise((resolve,reject)=>{
                                const r=new RFB(node,`ws://${location.host}/desktop/${channel}`);
                                r.viewOnly=false;
                                const timer=setTimeout(()=>{r.disconnect();reject(new Error('VNC handshake timeout'));},5000);
                                r.addEventListener('connect',()=>{
                                  r.sendKey(0x7a,'KeyZ');
                                  setTimeout(()=>{clearTimeout(timer);r.disconnect();node.remove();resolve();},300);
                                });
                              });
                            }""",
                                channel,
                            )

                        await inject("view")
                        snap = unpack(await call("browser_snapshot"))
                        assert "VIEW_ONLYz" not in snap["snapshot"], snap
                        await page.get_by_role("button", name="Take control", exact=True).click()
                        await page.locator("#status").filter(has_text="HUMAN").wait_for()
                        await inject("control")
                        snap = unpack(await call("browser_snapshot"))
                        assert "VIEW_ONLYz" in snap["snapshot"], snap
                        await page.get_by_role("button", name="Hand back to AI").click()
                        await page.locator("#status").filter(has_text="AGENT").wait_for()
                        await page.locator('#screen[data-connected="true"]').wait_for()
                        results.append(
                            "Server-enforced view-only rejects injected input; human channel accepts it"
                        )
                        await page.wait_for_function(
                            """() => {
                            const c=document.querySelector('#screen canvas');
                            if(!c || c.width!==1280)return false;
                            const d=c.getContext('2d').getImageData(0,0,1280,800).data;
                            const colors=new Set();
                            for(let i=0;i<d.length;i+=400)colors.add(`${d[i]},${d[i+1]},${d[i+2]}`);
                            return colors.size>30;
                        }""",
                            timeout=20000,
                        )
                        await page.screenshot(path=str(OUT / "viewer-before-controls.png"), full_page=True)
                        # CI headless browsers do not automatically grant clipboard access.
                        # First verify the denied-permission fallback, then actual copying.
                        await page.get_by_role("button", name="Copy endpoint").click()
                        await page.locator("#copy-feedback").filter(
                            has_text="Select and copy the endpoint manually."
                        ).wait_for()
                        await page.context.grant_permissions(
                            ["clipboard-read", "clipboard-write"], origin=URL
                        )
                        await page.get_by_role("button", name="Copy endpoint").click()
                        await page.locator("#copy-feedback").filter(has_text="Endpoint copied").wait_for()
                        assert await page.evaluate("navigator.clipboard.readText()") == URL + "/mcp"
                        await page.get_by_role("button", name="Full screen", exact=True).click()
                        await page.wait_for_function("() => !!document.fullscreenElement")
                        await page.evaluate("document.exitFullscreen()")
                        await page.wait_for_function("() => !document.fullscreenElement")
                        await page.get_by_role("button", name="Private takeover").click()
                        await page.locator("#status").filter(has_text="PRIVATE").wait_for()
                        await page.get_by_role("button", name="Hand back to AI").click()
                        await page.locator("#status").filter(has_text="AGENT").wait_for()
                        await page.get_by_role("button", name="Reconnect screen").click()
                        await page.locator('#screen[data-connected="true"]').wait_for()
                        await page.wait_for_function(
                            """() => {
                            const c=document.querySelector('#screen canvas');
                            if(!c || c.width!==1280 || c.height!==800)return false;
                            const d=c.getContext('2d').getImageData(0,0,1280,800).data;
                            const colors=new Set();
                            for(let i=0;i<d.length;i+=400)colors.add(`${d[i]},${d[i+1]},${d[i+2]}`);
                            return colors.size>30;
                        }""", timeout=20000)
                        await page.screenshot(path=str(OUT / "viewer.png"), full_page=True)
                        for width in [390, 768]:
                            await page.set_viewport_size({"width": width, "height": 844})
                            assert await page.evaluate(
                                "document.documentElement.scrollWidth <= window.innerWidth"
                            ), f"Horizontal overflow at {width}px"
                            await page.get_by_role("button", name="Pause AI").click()
                            await page.locator("#status").filter(has_text="PAUSED").wait_for()
                            await page.get_by_role("button", name="Hand back to AI").click()
                            await page.locator("#status").filter(has_text="AGENT").wait_for()
                            await page.wait_for_function(
                                """() => {
                                const c=document.querySelector('#screen canvas');
                                if(!c || c.width!==1280 || c.height!==800)return false;
                                const d=c.getContext('2d').getImageData(0,0,1280,800).data;
                                const colors=new Set();
                                for(let i=0;i<d.length;i+=400)colors.add(`${d[i]},${d[i+1]},${d[i+2]}`);
                                return colors.size>30;
                            }""", timeout=20000)
                            await page.screenshot(
                                path=str(OUT / f"viewer-{width}.png"), full_page=True
                            )
                        results.append("Responsive UI: copy, private mode, reconnect, 390px and 768px controls")
                        assert not errors, errors
                        await browser_ui.close()
                    results.append(
                        "Real noVNC viewer rendered, takeover/pause/resume and artifact UI verified"
                    )
                await http.post("/api/logout", headers=headers)
                assert (
                    await http.post("/mcp", headers={"Authorization": "Bearer " + token}, json={})
                ).status_code == 401
                results.append("Revocation immediately rejects previously valid MCP token")
    (OUT / ("restart-results.json" if restart else "results.json")).write_text(
        json.dumps({"passed": results}, indent=2)
    )
    print("\n".join("PASS " + r for r in results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart-check", action="store_true")
    asyncio.run(run(parser.parse_args().restart_check))
