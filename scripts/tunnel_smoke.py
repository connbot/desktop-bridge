"""Authenticated, disposable integration checks over a real public HTTPS origin."""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx
from websockets.sync.client import connect


def smoke(base, owner):
    with httpx.Client(base_url=base, timeout=45, follow_redirects=False) as http:
        denied = http.post("/mcp", json={})
        assert denied.status_code == 401
        assert base + "/.well-known/oauth-protected-resource" in denied.headers["www-authenticate"]
        metadata = http.get("/.well-known/oauth-protected-resource/mcp").json()
        assert metadata["resource"] == base + "/mcp"
        auth = http.get("/.well-known/oauth-authorization-server").json()
        assert auth["issuer"] == base and auth["token_endpoint_auth_methods_supported"] == ["none"]
        assert "S256" in auth["code_challenge_methods_supported"]
        registered = http.post("/register", json={
            "client_name": "Tunnel readiness check",
            "redirect_uris": ["http://127.0.0.1:43111/callback"],
            "token_endpoint_auth_method": "none",
        })
        registered.raise_for_status()
        client = registered.json()
        verifier = secrets.token_urlsafe(48)
        params = {
            "client_id": client["client_id"], "redirect_uri": client["redirect_uris"][0],
            "response_type": "code", "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="),
            "resource": base + "/mcp", "scope": "computer", "state": "tunnel-readiness",
        }
        redirect = http.get("/authorize", params=params)
        assert redirect.status_code in {302, 307}
        pending = parse_qs(urlsplit(redirect.headers["location"]).query)["authorize"][0]
        assert pending.startswith(base + "/authorize?"), pending
        login = http.post("/api/login", json={"token": owner})
        login.raise_for_status()
        assert "Secure" in login.headers["set-cookie"]
        csrf = login.json()["csrf"]
        headers = {"X-CSRF-Token": csrf, "Origin": base}
        approval = http.post("/authorize", data={**params, "csrf": csrf})
        assert approval.status_code == 303
        query = parse_qs(urlsplit(approval.headers["location"]).query)
        assert query["state"] == ["tunnel-readiness"]
        response = http.post("/token", data={
            "grant_type": "authorization_code", "code": query["code"][0],
            "client_id": client["client_id"], "redirect_uri": params["redirect_uri"],
            "resource": base + "/mcp", "code_verifier": verifier,
        })
        response.raise_for_status()
        bearer = response.json()["access_token"]
        mcp_headers = {"Authorization": "Bearer " + bearer, "Accept": "application/json, text/event-stream"}
        counter = 0

        def rpc(method, params):
            nonlocal counter
            counter += 1
            response = http.post("/mcp", headers=mcp_headers, json={
                "jsonrpc": "2.0", "id": counter, "method": method, "params": params,
            })
            response.raise_for_status()
            assert "application/json" in response.headers["content-type"], "Quick tunnels require JSON, not SSE"
            data = response.json()
            assert "error" not in data, data
            return data["result"]

        initialized = rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "tunnel-smoke", "version": "1"}})
        mcp_headers["MCP-Protocol-Version"] = initialized["protocolVersion"]
        names = {item["name"] for item in rpc("tools/list", {})["tools"]}
        assert {"desktop_screenshot", "browser_snapshot", "coding_exec_command", "session_start"} <= names
        assert not rpc("tools/call", {"name": "session_start", "arguments": {}}).get("isError")
        shot = rpc("tools/call", {"name": "desktop_screenshot", "arguments": {}})
        assert any(item["type"] == "image" for item in shot["content"])
        assert not rpc("tools/call", {"name": "browser_snapshot", "arguments": {}}).get("isError")
        command = rpc("tools/call", {"name": "coding_exec_command", "arguments": {
            "cmd": "printf tunnel-ready", "yield_time_ms": 1000, "bridge_action_id": "public-tunnel-smoke",
        }})
        assert not command.get("isError") and "tunnel-ready" in str(command)
        cookie = "; ".join(f"{k}={v}" for k, v in http.cookies.items())
        with connect(base.replace("https://", "wss://", 1) + "/desktop/view", origin=base,
                     additional_headers={"Cookie": cookie}, subprotocols=["binary"], open_timeout=30) as ws:
            assert ws.recv(timeout=10).startswith(b"RFB ")
        # Leave a clean, ready-to-authorize UI. No test access token remains valid.
        http.post("/api/control/paused", headers=headers).raise_for_status()
        http.post("/api/logout", headers=headers).raise_for_status()
        assert http.post("/mcp", headers=mcp_headers, json={}).status_code == 401
    print("PASS public HTTPS: OAuth discovery, PKCE, JSON MCP, real screenshot/browser/shell, VNC WebSocket, revocation", flush=True)
