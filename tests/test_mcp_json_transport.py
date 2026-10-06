"""In-process transport checks; no desktop, public tunnel, or account is used."""

import base64
import hashlib
import json
from datetime import timedelta

import anyio
import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from test_app import TOKEN, FakeBrowser, FakeCoding, FakeDesktop

from desktop_bridge.app import Runtime, create_app
from desktop_bridge.context_store import DisabledContextStore
from desktop_bridge.plugins import MCPPlugins

BASE = "https://bridge.example"


@pytest.fixture
def transport_app(tmp_path):
    runtime = Runtime(
        tmp_path, desktop=FakeDesktop(), browser=FakeBrowser(), coding=FakeCoding(),
        context=DisabledContextStore(), plugins=MCPPlugins(),
    )
    app = create_app(tmp_path, TOKEN, BASE, runtime)
    # Issue a disposable local grant through the real Auth implementation.
    # OAuth's HTTP login/consent journey is covered in test_app.py.
    auth = app.state.auth
    client = auth.register({"redirect_uris": ["https://client.example/callback"]})
    verifier = "v" * 43
    params = {
        "client_id": client["client_id"], "redirect_uri": client["redirect_uris"][0],
        "response_type": "code", "code_challenge_method": "S256",
        "code_challenge": base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("="),
        "resource": BASE + "/mcp", "scope": "computer",
    }
    token = auth.exchange({
        **params, "grant_type": "authorization_code", "code": auth.approve(params),
        "code_verifier": verifier,
    })["access_token"]
    return app, token


@pytest.mark.parametrize("protocol", ["2025-03-26", "2025-11-25"])
async def test_json_protocol_lifecycle_and_get_rejection(transport_app, protocol):
    app, token = transport_app
    with anyio.fail_after(5):
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE,
        ) as client:
            for bearer in (None, TOKEN, "invalid-access-token"):
                headers = {"Accept": "text/event-stream"}
                if bearer:
                    headers["Authorization"] = "Bearer " + bearer
                response = await client.get("/mcp", headers=headers)
                assert response.status_code == 401
                assert "oauth-protected-resource" in response.headers["www-authenticate"]

            headers = {
                "Authorization": "Bearer " + token,
                "Accept": "application/json, text/event-stream",
            }

            async def rpc(method, params, request_id):
                response = await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
                })
                assert response.status_code == 200, response.text
                assert response.headers["content-type"].startswith("application/json")
                assert "mcp-session-id" not in response.headers
                payload = response.json()
                assert payload["id"] == request_id and "error" not in payload
                return payload["result"]

            initialized = await rpc("initialize", {
                "protocolVersion": protocol, "capabilities": {},
                "clientInfo": {"name": "json-transport-regression", "version": "1"},
            }, 1)
            headers["MCP-Protocol-Version"] = initialized["protocolVersion"]
            notification = await client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "method": "notifications/initialized",
            })
            assert notification.status_code == 202
            assert notification.content == b""

            for accept in ("text/event-stream", "application/json, text/event-stream"):
                response = await client.get("/mcp", headers={**headers, "Accept": accept})
                assert response.status_code == 405
                assert set(response.headers["allow"].split(", ")) == {"POST", "DELETE"}
                assert "text/event-stream" not in response.headers.get("content-type", "")
                assert response.headers["cache-control"] == "no-store"

            listed = await rpc("tools/list", {}, 2)
            assert "session_status" in {item["name"] for item in listed["tools"]}
            result = await rpc("tools/call", {"name": "session_status", "arguments": {}}, 3)
            assert not result.get("isError")
            assert json.loads(result["content"][0]["text"])["state"] == "READY"


async def test_real_sdk_continues_json_posts_after_get_405(transport_app):
    app, token = transport_app
    exchanges = []

    async def record_response(response):
        if response.request.method == "POST" and response.request.url.path == "/mcp":
            await response.aread()
            exchanges.append((
                json.loads(response.request.content)["method"], response.status_code,
                response.headers.get("content-type", ""), response.content,
            ))

    with anyio.fail_after(5):
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE,
            headers={"Authorization": "Bearer " + token},
            event_hooks={"response": [record_response]},
        ) as http:
            response = await http.get("/mcp", headers={"Accept": "text/event-stream"})
            assert response.status_code == 405
            async with streamable_http_client(BASE + "/mcp", http_client=http) as streams:
                async with ClientSession(
                    streams[0], streams[1], read_timeout_seconds=timedelta(seconds=2),
                ) as client:
                    await client.initialize()
                    assert streams[2]() is None
                    assert "session_status" in {tool.name for tool in (await client.list_tools()).tools}
                    result = await client.call_tool("session_status", {})
                    assert not result.isError
                    assert json.loads(result.content[0].text)["state"] == "READY"

    assert [exchange[0] for exchange in exchanges] == [
        "initialize", "notifications/initialized", "tools/list", "tools/call",
    ]
    for method, status, content_type, body in exchanges:
        if method == "notifications/initialized":
            assert status == 202 and body == b""
        else:
            assert status == 200 and content_type.startswith("application/json")
