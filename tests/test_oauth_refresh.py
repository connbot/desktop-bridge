"""Offline HTTP/SDK refresh regressions with fictional credentials and a fake clock."""

import base64
import hashlib
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata
from test_app import TOKEN, FakeBrowser, FakeCoding, FakeDesktop

import desktop_bridge.auth as auth_module
from desktop_bridge.app import Runtime, create_app

BASE = "https://bridge.example"


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    fake = SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic)
    monkeypatch.setattr(auth_module, "time", fake)
    return now, fake


@pytest.fixture
def app(tmp_path):
    runtime = Runtime(tmp_path, desktop=FakeDesktop(), browser=FakeBrowser(), coding=FakeCoding())
    return create_app(tmp_path, TOKEN, BASE, runtime)


def issue(client, *, renewable=True):
    csrf = client.post("/api/login", json={"token": TOKEN}).json()["csrf"]
    metadata = {"redirect_uris": ["https://client.example/callback"]}
    if renewable:
        metadata["grant_types"] = ["authorization_code", "refresh_token"]
    registered = client.post("/register", json=metadata).json()
    verifier = "v" * 43
    params = {
        "client_id": registered["client_id"],
        "redirect_uri": registered["redirect_uris"][0],
        "response_type": "code",
        "code_challenge_method": "S256",
        "code_challenge": base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("="),
        "scope": "computer",
        "resource": BASE + "/mcp",
    }
    consent = client.get("/authorize", params=params)
    assert consent.status_code == 200
    if renewable:
        assert "up to 30 days" in consent.text and "unused for 7 days" in consent.text
        assert "Approve renewable access" in consent.text
    else:
        assert "Approve for one hour" in consent.text
    response = client.post(
        "/authorize", data={**params, "csrf": csrf}, follow_redirects=False
    )
    code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
    response = client.post("/token", data={
        **params, "grant_type": "authorization_code", "code": code,
        "code_verifier": verifier,
    })
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    return registered["client_id"], csrf, response.json()


def test_http_refresh_after_one_hour_and_logout(app, clock, caplog):
    now, _ = clock
    with TestClient(app, base_url=BASE) as client:
        metadata = client.get("/.well-known/oauth-authorization-server").json()
        assert metadata["grant_types_supported"] == ["authorization_code", "refresh_token"]
        client_id, csrf, initial = issue(client)
        now[0] += 3601
        assert client.get("/mcp", headers={
            "Authorization": "Bearer " + initial["access_token"],
        }).status_code == 401
        response = client.post("/token", data={
            "grant_type": "refresh_token", "client_id": client_id,
            "refresh_token": initial["refresh_token"], "resource": BASE + "/mcp",
            "scope": "computer",
        })
        assert response.status_code == 200
        renewed = response.json()
        assert renewed["refresh_token"] != initial["refresh_token"]
        assert renewed["access_token"] != initial["access_token"]
        assert renewed["expires_in"] == 3600
        assert app.state.auth.bearer(renewed["access_token"])
        assert client.post("/api/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
        response = client.post("/token", data={
            "grant_type": "refresh_token", "client_id": client_id,
            "refresh_token": renewed["refresh_token"],
        })
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"
        assert response.json()["error_description"]
        assert response.headers["pragma"] == "no-cache"
        assert not app.state.auth.bearer(renewed["access_token"])
        for pair in [initial, renewed]:
            assert pair["access_token"] not in caplog.text
            assert pair["refresh_token"] not in caplog.text


def test_http_code_only_does_not_gain_renewable_access(app, clock):
    now, _ = clock
    with TestClient(app, base_url=BASE) as client:
        client_id, csrf, initial = issue(client, renewable=False)
        assert "refresh_token" not in initial
        now[0] += 3601
        assert not app.state.auth.bearer(initial["access_token"])
        response = client.post("/token", data={
            "grant_type": "refresh_token", "client_id": client_id,
            "refresh_token": initial["access_token"],
        })
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"
        # Reapproving a reused, old code-only client ID cannot upgrade its grants.
        params = {
            "client_id": client_id, "redirect_uri": "https://client.example/callback",
            "response_type": "code", "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(
                hashlib.sha256(("v" * 43).encode()).digest()
            ).decode().rstrip("="),
        }
        assert "Approve for one hour" in client.get("/authorize", params=params).text
        response = client.post(
            "/authorize", data={**params, "csrf": csrf}, follow_redirects=False
        )
        code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
        renewed = client.post("/token", data={
            **params, "grant_type": "authorization_code", "code": code,
            "code_verifier": "v" * 43,
        })
        assert renewed.status_code == 200 and "refresh_token" not in renewed.json()


def test_duplicate_token_parameters_rejected_without_burning_refresh(app):
    with TestClient(app, base_url=BASE) as client:
        client_id, _, initial = issue(client)
        request = (
            f"grant_type=refresh_token&client_id={client_id}"
            f"&refresh_token={initial['refresh_token']}&refresh_token=bad"
        )
        response = client.post("/token", content=request, headers={
            "Content-Type": "application/x-www-form-urlencoded",
        })
        assert response.status_code == 400 and response.json()["error"] == "invalid_request"
        assert client.post("/token", data={
            "grant_type": "refresh_token", "client_id": client_id,
            "refresh_token": initial["refresh_token"],
        }).status_code == 200


class MemoryStorage:
    tokens = None
    client = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, client_info):
        self.client = client_info


async def test_official_sdk_discovers_registers_and_refreshes_without_reconsent(
    app, clock, monkeypatch,
):
    import mcp.client.auth.oauth2 as sdk_oauth
    import mcp.shared.auth_utils as sdk_utils

    now, fake = clock
    monkeypatch.setattr(sdk_oauth, "time", fake)
    monkeypatch.setattr(sdk_utils, "time", fake)
    storage = MemoryStorage()
    callbacks = []
    grants = []
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE,
    ) as owner:
        csrf = (await owner.post("/api/login", json={"token": TOKEN})).json()["csrf"]

        async def redirect_handler(url):
            # Simulate only the test owner's explicit consent, on the in-process app.
            query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
            response = await owner.post("/authorize", data={**query, "csrf": csrf})
            assert response.status_code == 303
            callbacks.append(parse_qs(urlsplit(response.headers["location"]).query))

        async def callback_handler():
            return callbacks[-1]["code"][0], callbacks[-1]["state"][0]

        async def record_request(request):
            if request.url.path == "/token":
                # Record grant type only, never credentials.
                grants.append(parse_qs(request.content.decode())["grant_type"][0])

        provider = OAuthClientProvider(
            BASE + "/mcp", OAuthClientMetadata(
                redirect_uris=["https://client.example/callback"],
                token_endpoint_auth_method="none", scope="computer",
            ), storage, redirect_handler, callback_handler,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE, auth=provider,
            headers={"Accept": "application/json, text/event-stream"},
            event_hooks={"request": [record_request]},
        ) as client:
            response = await client.post("/mcp", json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "offline-refresh-test", "version": "1"},
                },
            })
            assert response.status_code == 200
            first_access = storage.tokens.access_token
            first_refresh = storage.tokens.refresh_token
            assert first_refresh
            for request_id in [2, 3]:
                now[0] += 3601
                response = await client.post("/mcp", json={
                    "jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": {},
                })
                assert response.status_code == 200
                assert "session_status" in {
                    item["name"] for item in response.json()["result"]["tools"]
                }
            assert len(callbacks) == 1
            assert grants == ["authorization_code", "refresh_token", "refresh_token"]
            assert storage.tokens.access_token != first_access
            assert storage.tokens.refresh_token != first_refresh
            assert not app.state.auth.bearer(first_access)
            assert app.state.auth.bearer(storage.tokens.access_token)


@pytest.mark.parametrize("grant_types", [
    [], ["refresh_token"], ["authorization_code", "client_credentials"],
    "authorization_code", None, ["authorization_code", {}],
])
def test_registration_rejects_unsupported_grants(app, grant_types):
    with TestClient(app, base_url=BASE) as client:
        response = client.post("/register", json={
            "redirect_uris": ["https://client.example/callback"], "grant_types": grant_types,
        })
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_client"
