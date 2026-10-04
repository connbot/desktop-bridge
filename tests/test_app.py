import base64
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from mcp import types

from desktop_bridge.app import Runtime, create_app
from desktop_bridge.state import BridgeError

TOKEN = "test-only-owner-token-not-for-deployment-123"


class FakeDesktop:
    calls = 0

    async def screenshot(self):
        return b"not-a-real-png-test-fixture"

    async def perform(self, payload):
        self.calls += 1
        return {"ok": True}


class FakeBrowser:
    async def connect(self):
        pass

    async def close(self):
        pass

    async def snapshot(self):
        return {"url": "about:blank", "snapshot": '- button "Test"'}

    async def perform(self, payload):
        return {"ok": True}


class FakeCoding:
    tools = [
        types.Tool(
            name="read_file",
            description="Read",
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
    ]

    async def connect(self):
        pass

    async def close(self):
        pass

    async def call(self, name, args):
        return types.CallToolResult(content=[types.TextContent(type="text", text="hello")])


@pytest.fixture
def app(tmp_path):
    runtime = Runtime(tmp_path, desktop=FakeDesktop(), browser=FakeBrowser(), coding=FakeCoding())
    return create_app(tmp_path, TOKEN, "http://testserver", runtime)


def login(client):
    r = client.post("/api/login", json={"token": TOKEN})
    assert r.status_code == 200
    return {"X-CSRF-Token": r.json()["csrf"]}


def test_http_auth_and_csrf(app):
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 401
        assert c.post("/mcp", json={}).status_code == 401
        headers = login(c)
        assert c.get("/api/status").json()["state"] == "READY"
        assert c.post("/api/control/human").status_code == 401
        assert (
            c.post(
                "/api/control/human", headers={**headers, "Origin": "https://evil.test"}
            ).status_code
            == 401
        )
        assert c.post("/api/control/human", headers=headers).status_code == 200
        assert c.get("/api/status").json()["state"] == "HUMAN"
        c.post("/api/logout", headers=headers)
        assert c.get("/api/status").status_code == 401


def test_oauth_roundtrip_and_mcp(app):
    with TestClient(app) as c:
        headers = login(c)
        client = c.post("/register", json={"redirect_uris": ["http://127.0.0.1:9999/cb"]}).json()
        verifier = "v" * 43
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        params = {
            "client_id": client["client_id"],
            "redirect_uri": client["redirect_uris"][0],
            "response_type": "code",
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": "original",
        }
        consent = c.get("/authorize", params=params)
        assert consent.status_code == 200
        assert "form-action" not in consent.headers["content-security-policy"]
        response = c.post(
            "/authorize", data={**params, "csrf": headers["X-CSRF-Token"]}, follow_redirects=False
        )
        from urllib.parse import parse_qs, urlsplit

        result = parse_qs(urlsplit(response.headers["location"]).query)
        assert result["state"] == ["original"]
        token = c.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": result["code"][0],
                "client_id": client["client_id"],
                "redirect_uri": params["redirect_uri"],
                "code_verifier": verifier,
            },
        ).json()["access_token"]
        mcp_headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
        }
        response = c.post(
            "/mcp",
            headers=mcp_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["serverInfo"]["name"] == "desktop-bridge"
        result = c.post(
            "/mcp",
            headers=mcp_headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert any(t["name"] == "coding_read_file" for t in result.json()["result"]["tools"])
        c.post("/api/logout", headers=headers)
        assert c.post("/mcp", headers=mcp_headers, json={}).status_code == 401


async def test_private_observation_and_pause_cannot_be_overridden(app):
    r = app.state.runtime
    await r.call("session_start", {})
    shot = await r.call("desktop_screenshot", {})
    obs = json.loads(shot[0].text)["observation_id"]
    action = {
        "action_id": "a",
        "observation_id": obs,
        "action": {"kind": "click", "x": 10, "y": 20},
    }
    await r.call("desktop_action", action)
    await r.call("desktop_action", action)
    assert r.desktop.calls == 1
    await r.session.transition("private")
    for name in ["desktop_screenshot", "browser_snapshot", "artifacts_list", "session_start"]:
        with pytest.raises(BridgeError):
            await r.call(name, {})
    await r.session.transition("paused")
    with pytest.raises(BridgeError):
        await r.call("session_start", {})
    r.session.close()


def test_artifacts_and_traversal(app, tmp_path):
    (tmp_path / "workspace" / "hello.txt").write_text("hello")
    (tmp_path / "outside.txt").write_text("private")
    (tmp_path / "workspace" / "leak").symlink_to(tmp_path / "outside.txt")
    with TestClient(app) as c:
        login(c)
        assert c.get("/api/artifacts/hello.txt").content == b"hello"
        assert c.get("/api/artifacts/leak").status_code == 404
        assert [x["path"] for x in c.get("/api/artifacts").json()["files"]] == ["hello.txt"]


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "click", "x": 1280},
        {"kind": "click", "x": True},
        {"kind": "drag", "path": [[0, 0], [1, 900]]},
        {"kind": "key", "keys": ["ctrl-alt-delete"]},
        {"kind": "type", "text": "x" * 20001},
    ],
)
def test_action_validation(payload):
    from pydantic import ValidationError

    from desktop_bridge.models import DesktopAction

    with pytest.raises(ValidationError):
        DesktopAction.model_validate(payload)


async def test_agent_cannot_exit_private_by_stop(app):
    r = app.state.runtime
    await r.session.transition("private")
    with pytest.raises(BridgeError):
        await r.call("session_stop", {})
    assert r.session.mode == "private"
    r.session.close()


async def test_cancellation_drains_before_releasing():
    import asyncio

    from desktop_bridge.app import drain_on_cancel

    started, finish = asyncio.Event(), asyncio.Event()

    async def operation():
        started.set()
        await finish.wait()

    task = asyncio.create_task(drain_on_cancel(operation()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_limits_hosts_and_websocket_auth(app):
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as c:
        assert c.get("/healthz", headers={"Host": "evil.example"}).status_code == 400
        assert c.post("/register", content=b"x" * (4 * 1024 * 1024 + 1)).status_code == 413
        assert c.post("/api/login", json=[]).status_code == 401
        assert c.post("/register", json=[]).status_code == 400
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/desktop/control", headers={"Origin": "http://testserver"}):
                pytest.fail("Unauthenticated websocket accepted")
        login(c)
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/desktop/view", headers={"Origin": "https://evil.example"}):
                pytest.fail("Foreign-origin websocket accepted")
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/desktop/control", headers={"Origin": "http://testserver"}):
                pytest.fail("Human write socket accepted while agent owns control")


def test_oauth_login_redirect_uses_public_https_origin_behind_proxy(tmp_path):
    from urllib.parse import parse_qs, urlsplit

    runtime = Runtime(tmp_path, desktop=FakeDesktop(), browser=FakeBrowser(), coding=FakeCoding())
    app = create_app(tmp_path, TOKEN, "https://bridge.example", runtime)
    # Tunnel terminates TLS; origin request is plain HTTP, with an untrusted
    # X-Forwarded-Proto value. The configured origin is still authoritative.
    with TestClient(app, base_url="http://bridge.example") as client:
        registered = client.post("/register", json={"redirect_uris": ["https://client.test/callback"]}).json()
        response = client.get("/authorize", params={
            "client_id": registered["client_id"], "redirect_uri": registered["redirect_uris"][0],
            "response_type": "code", "code_challenge_method": "S256", "code_challenge": "a" * 43,
        }, headers={"X-Forwarded-Proto": "http"}, follow_redirects=False)
        target = parse_qs(urlsplit(response.headers["location"]).query)["authorize"][0]
        assert target.startswith("https://bridge.example/authorize?")
