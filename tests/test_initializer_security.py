"""Independent, local-only initializer security and race regressions.

Provider APIs and DNS are mocked. RSA test keys and sealed-box fixtures are
fresh, fictional, and never persisted. No account, tunnel, or workflow is used.
"""

import asyncio
import base64
import importlib
import json
import os
import socket
import time
from contextlib import aclosing
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from nacl.public import PrivateKey, SealedBox

from initializer.config import Config
from initializer.mock import MockProviders
from initializer.network import accepted_origin, probe
from initializer.providers import ProviderError, Providers, oauth_challenge, sealed

# Import the ASGI module without touching an operator database or constructing a
# live provider client. Restore the caller's environment immediately afterward.
with patch.dict(
    os.environ,
    {
        "INITIALIZER_MODE": "mock",
        "INITIALIZER_DATABASE": ":memory:",
        "INITIALIZER_ORIGIN": "http://127.0.0.1:8765",
    },
):
    create_app = importlib.import_module("initializer.app").create_app

ORIGIN = "http://127.0.0.1:8765"


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


async def login(c):
    r = await c.get("/api/session")
    headers = {"Origin": ORIGIN, "X-CSRF-Token": r.json()["csrf"]}
    assert (await c.post("/api/auth/github", headers=headers, json={})).status_code == 200
    return headers


@pytest.mark.asyncio
async def test_named_double_click_has_one_operation():
    class RaceProvider(MockProviders):
        armed = False

        async def zones(self, token):
            if self.armed:
                await asyncio.sleep(0.04)
            return await super().zones(token)

    p = RaceProvider()
    app = create_app(Config(mode="mock", database=":memory:"), providers=p)
    async with client(app) as c:
        h = await login(c)
        assert (await c.post("/api/auth/cloudflare", headers=h, json={})).status_code == 200
        p.armed = True
        body = {
            "saved_password": True,
            "accepted_preview": True,
            "mode": "named",
            "zone_id": "zone-1",
        }
        replies = await asyncio.gather(
            *[c.post("/api/operations", headers=h, json=body) for _ in range(2)]
        )
        ids = [r.json()["id"] for r in replies]
        await asyncio.sleep(0.03)
        assert len(set(ids)) == 1, {
            "ids": ids,
            "ledger_count": len(app.state.store.all()),
            "calls": p.calls,
        }
        assert p.calls.count("create_repository") == 1


class RunProvider(MockProviders):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block_probe = False

    async def oidc_claims(self, token):
        return {
            "repository_id": "123",
            "repository_owner_id": "42",
            "run_id": "987",
            "run_attempt": "1",
            "sha": "a" * 40,
            "event_name": "workflow_dispatch",
            "ref": "refs/heads/main",
            "workflow_ref": "preview-user/desktop-bridge-abcdef123456/.github/workflows/preview.yml@refs/heads/main",
        }


async def fixture_run(p, *, probe=None):
    app = create_app(Config(mode="live", database=":memory:"), providers=p, readiness_probe=probe)
    op = {
        "id": "op1",
        "owner_id": 42,
        "owner_login": "preview-user",
        "installation_id": 101,
        "repo_name": "desktop-bridge-abcdef123456",
        "repo_id": 123,
        "branch": "main",
        "commit": "a" * 40,
        "mode": "quick",
        "stage": "waiting_ready",
        "created_at": time.time(),
        "last_sequence": 0,
        "run_id": 987,
    }
    p.runs[987] = {
        "id": 987,
        "status": "in_progress",
        "head_sha": "a" * 40,
        "run_attempt": 1,
        "event": "workflow_dispatch",
    }
    app.state.store.save(op)
    c = client(app)
    r = await c.get("/api/session")
    sid = c.cookies["bridge_initializer_unconfigured"]
    s = app.state.sessions[sid]
    s.update(
        user={"id": 42, "login": "preview-user"}, installation_id=101, github_token="fictional-test"
    )
    h = {"Origin": ORIGIN, "X-CSRF-Token": r.json()["csrf"]}
    return app, c, h


@pytest.mark.asyncio
async def test_inflight_ready_cannot_resurrect_stopped():
    p = RunProvider()

    async def probe(origin, op):
        p.entered.set()
        await p.release.wait()

    app, c, h = await fixture_run(p, probe=probe)
    async with aclosing(c):
        body = {
            "event": "ready",
            "sequence": 1,
            "expires_at": time.time() + 3500,
            "smoke_passed": True,
            "origin": "https://fictional-preview.trycloudflare.com",
        }
        ready = asyncio.create_task(
            c.post(
                "/api/run-events/op1", headers={"Authorization": "Bearer fictional-test"}, json=body
            )
        )
        await asyncio.wait_for(p.entered.wait(), 1)
        stop = asyncio.create_task(c.post("/api/operations/op1/stop", headers=h, json={}))
        await asyncio.sleep(0.04)
        p.release.set()
        results = await asyncio.gather(ready, stop)
        result = app.state.store.get("op1")
        assert result["stage"] in {"stopping", "ended"}, {
            "stage": result["stage"],
            "responses": [r.status_code for r in results],
            "calls": p.calls,
        }
        assert "origin" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["ending", "checking_status"])
async def test_pending_status_can_reconcile_terminal(stage):
    p = RunProvider()
    app, c, h = await fixture_run(p)
    op = app.state.store.get("op1")
    op["stage"] = stage
    app.state.store.save(op)
    p.runs[987].update(status="completed", conclusion="success")
    async with aclosing(c):
        result = await c.get("/api/operations/op1")
        assert result.json()["stage"] == "ended", result.json()


@pytest.mark.asyncio
async def test_csrf_and_owner_isolation():
    p = MockProviders()
    app = create_app(Config(mode="mock", database=":memory:"), providers=p)
    async with client(app) as c:
        h = await login(c)
        body = {"saved_password": True, "accepted_preview": True, "mode": "quick"}
        for bad in [
            {},
            {"Origin": "https://foreign.test", "X-CSRF-Token": h["X-CSRF-Token"]},
            {"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
        ]:
            assert (await c.post("/api/operations", headers=bad, json=body)).status_code == 403
        assert p.calls == []
        r = await c.post("/api/operations", headers=h, json=body)
        opid = r.json()["id"]
        await asyncio.sleep(0.02)
        sid = c.cookies["bridge_initializer_mock"]
        app.state.sessions[sid]["user"] = {"id": 999, "login": "other-user"}
        for path, method in [("", c.get), ("/start", c.post), ("/stop", c.post)]:
            r = await method(
                "/api/operations/" + opid + path, headers=h, **({"json": {}} if path else {})
            )
            assert r.status_code == 409
        assert p.calls.count("create_repository") == 1


@pytest.mark.asyncio
async def test_unknown_create_never_blindly_retries():
    p = MockProviders()
    p.scenario = "create_unknown"
    app = create_app(Config(mode="mock", database=":memory:"), providers=p)
    async with client(app) as c:
        h = await login(c)
        body = {"saved_password": True, "accepted_preview": True, "mode": "quick"}
        r = await c.post("/api/operations", headers=h, json=body)
        opid = r.json()["id"]
        await asyncio.sleep(0.02)
        for _ in range(3):
            r = await c.post("/api/operations", headers=h, json=body)
            assert r.json()["id"] == opid and r.json()["stage"] == "unknown"
            await c.get("/api/operations/" + opid)
        assert p.calls == ["create_repository"]


@pytest.mark.asyncio
async def test_chunked_body_limit_is_enforced():
    p = MockProviders()
    app = create_app(Config(mode="mock", database=":memory:"), providers=p)
    async with client(app) as c:
        h = await login(c)

        async def body():
            yield b'{"saved_password":true,"accepted_preview":true,"mode":"quick","padding":"'
            for _ in range(50):
                yield b"x" * 4096
            yield b'"}'

        r = await c.post(
            "/api/operations", headers={**h, "Content-Type": "application/json"}, content=body()
        )
        assert r.status_code == 413, {"status": r.status_code, "response": r.text[:200]}
        assert p.calls == []


@pytest.mark.asyncio
async def test_unknown_operation_cannot_allocate_locks():
    app = create_app(Config(mode="mock", database=":memory:"), providers=MockProviders())
    endpoint = next(
        r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/operations/{op_id}"
    )
    closure = dict(
        zip(
            endpoint.__code__.co_freevars,
            [cell.cell_contents for cell in endpoint.__closure__],
            strict=False,
        )
    )
    locks = closure["locks"]
    baseline = len(locks)
    async with client(app) as c:
        for i in range(7):
            assert (await c.get(f"/api/operations/nonexistent-{i}")).status_code in {401, 409}
            assert (
                await c.post(
                    f"/api/run-events/nonexistent-{i}",
                    headers={"Authorization": "Bearer fictional-invalid"},
                    json={},
                )
            ).status_code == 409
    assert len(locks) == baseline, {"before": baseline, "after": len(locks)}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,bad",
    [
        ("repository_id", "999"),
        ("repository_owner_id", "999"),
        ("run_id", "999"),
        ("run_attempt", "2"),
        ("sha", "b" * 40),
        ("event_name", "push"),
        ("ref", "refs/heads/evil"),
        ("workflow_ref", "attacker/repo/.github/workflows/preview.yml@refs/heads/main"),
    ],
)
async def test_ready_claim_binding_rejects_wrong_run_identity(key, bad):
    p = RunProvider()
    original = p.oidc_claims

    async def claims(token):
        data = await original(token)
        data[key] = bad
        return data

    p.oidc_claims = claims

    async def must_not_probe(origin, op):
        raise AssertionError("Invalid claims must not reach probe")

    app, c, h = await fixture_run(p, probe=must_not_probe)
    async with aclosing(c):
        body = {
            "event": "ready",
            "sequence": 1,
            "expires_at": time.time() + 3500,
            "smoke_passed": True,
            "origin": "https://fictional-preview.trycloudflare.com",
        }
        r = await c.post(
            "/api/run-events/op1", headers={"Authorization": "Bearer fictional-test"}, json=body
        )
        assert r.status_code == 409 and r.json()["code"] == "invalid_run_identity"
        assert app.state.store.get("op1")["stage"] == "waiting_ready"


@pytest.mark.asyncio
async def test_duplicate_ready_cannot_extend_lifetime_or_reprobe():
    p = RunProvider()
    calls = []

    async def probe(origin, op):
        calls.append(origin)

    app, c, h = await fixture_run(p, probe=probe)
    async with aclosing(c):
        expiry = time.time() + 3500
        body = {
            "event": "ready",
            "sequence": 1,
            "expires_at": expiry,
            "smoke_passed": True,
            "origin": "https://fictional-preview.trycloudflare.com",
        }
        r = await c.post(
            "/api/run-events/op1", headers={"Authorization": "Bearer fictional-test"}, json=body
        )
        assert r.status_code == 200
        body["expires_at"] += 50
        r = await c.post(
            "/api/run-events/op1", headers={"Authorization": "Bearer fictional-test"}, json=body
        )
        assert r.status_code == 409
        assert app.state.store.get("op1")["expires_at"] == expiry and len(calls) == 1


@pytest.mark.asyncio
async def test_stale_ready_heartbeat_cannot_claim_online():
    p = RunProvider()
    app, c, h = await fixture_run(p)
    op = app.state.store.get("op1")
    op.update(
        stage="ready",
        last_seen=time.time() - 180,
        expires_at=time.time() + 3000,
        origin="https://fictional-preview.trycloudflare.com",
    )
    app.state.store.save(op)
    async with aclosing(c):
        r = await c.get("/api/operations/op1")
        assert r.json()["stage"] != "ready" and not r.json().get("origin"), r.json()


@pytest.mark.asyncio
async def test_failed_heartbeat_probe_hides_existing_ready():
    from initializer.providers import ProviderError

    p = RunProvider()

    async def offline(origin, op):
        raise ProviderError("readiness_pending")

    app, c, h = await fixture_run(p, probe=offline)
    op = app.state.store.get("op1")
    op.update(
        stage="ready",
        last_seen=time.time(),
        expires_at=time.time() + 3000,
        origin="https://fictional-preview.trycloudflare.com",
    )
    app.state.store.save(op)
    async with aclosing(c):
        r = await c.post(
            "/api/run-events/op1",
            headers={"Authorization": "Bearer fictional-test"},
            json={
                "event": "ready",
                "sequence": 2,
                "expires_at": time.time() + 3000,
                "smoke_passed": True,
                "origin": "https://fictional-preview.trycloudflare.com",
            },
        )
        assert r.status_code == 409
        latest = app.state.store.get("op1")
        assert latest["stage"] != "ready" and not latest.get("origin"), latest


@pytest.mark.asyncio
async def test_restart_single_dispatch_rejects_old_run_callback():
    p = RunProvider()

    async def dispatch(op, token):
        p.calls.append("dispatch")
        p.runs[988] = {
            "id": 988,
            "status": "in_progress",
            "head_sha": "a" * 40,
            "run_attempt": 1,
            "event": "workflow_dispatch",
        }
        return {"run_id": 988, "run_url": "https://github.com/fictional/repo/actions/runs/988"}

    p.dispatch = dispatch

    async def probe(origin, op):
        pass

    app, c, h = await fixture_run(p, probe=probe)
    old = app.state.store.get("op1")
    old.update(
        stage="ended",
        secret_written=True,
        last_sequence=99,
        expires_at=time.time() - 20,
        origin="https://old.trycloudflare.com",
    )
    app.state.store.save(old)
    p.runs[987].update(status="completed", conclusion="success")
    async with aclosing(c):
        replies = await asyncio.gather(
            *[
                c.post("/api/operations/op1/restart", headers=h, json={"accepted_preview": True})
                for _ in range(2)
            ]
        )
        await asyncio.sleep(0.02)
        assert sorted(r.status_code for r in replies) == [200, 409]
        current = app.state.store.get("op1")
        assert (
            current["run_id"] == 988 and current["last_sequence"] == 0 and not current.get("origin")
        )
        assert p.calls == ["dispatch"]
        body = {
            "event": "ready",
            "sequence": 100,
            "expires_at": time.time() + 3500,
            "smoke_passed": True,
            "origin": "https://old.trycloudflare.com",
        }
        r = await c.post(
            "/api/run-events/op1", headers={"Authorization": "Bearer fictional-old-run"}, json=body
        )
        assert r.status_code == 409 and r.json()["code"] == "invalid_run_identity"
        assert app.state.store.get("op1")["stage"] == "waiting_ready"


@pytest.mark.asyncio
async def test_unknown_outcome_blocks_retry_restart():
    p = RunProvider()
    app, c, h = await fixture_run(p)
    op = app.state.store.get("op1")
    op.update(stage="unknown", secret_written=True, uncertain=True, error="provider_unavailable")
    app.state.store.save(op)
    async with aclosing(c):
        for action in ["retry", "restart"]:
            r = await c.post(
                "/api/operations/op1/" + action, headers=h, json={"accepted_preview": True}
            )
            assert r.status_code == 409
    assert p.calls == []


@pytest.mark.asyncio
async def test_retry_keeps_existing_repository():
    p = MockProviders()
    app = create_app(Config(mode="mock", database=":memory:"), providers=p)
    async with client(app) as c:
        h = await login(c)
        p.scenario = "actions_disabled"
        r = await c.post(
            "/api/operations",
            headers=h,
            json={"saved_password": True, "accepted_preview": True, "mode": "quick"},
        )
        opid = r.json()["id"]
        await asyncio.sleep(0.02)
        op = app.state.store.get(opid)
        assert op["stage"] == "failed" and op.get("repo_id")
        p.scenario = "success"
        r = await c.post("/api/operations/" + opid + "/retry", headers=h, json={})
        assert r.status_code == 200
        await asyncio.sleep(0.02)
        current = app.state.store.get(opid)
        assert current["stage"] == "awaiting_password" and current["repo_id"] == op["repo_id"]
        assert p.calls.count("create_repository") == 1


def test_sealed_box_roundtrip_contains_no_plaintext():
    key = PrivateKey.generate()
    value = "FICTIONAL-TEST-OWNER-" + "x" * 43
    encrypted = sealed(value, base64.b64encode(bytes(key.public_key)).decode())
    raw = base64.b64decode(encrypted)
    assert value.encode() not in raw
    assert SealedBox(key).decrypt(raw).decode() == value


@pytest.mark.parametrize(
    "origin",
    [
        "http://test.trycloudflare.com",
        "https://test.trycloudflare.com.evil.test",
        "https://trycloudflare.com",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://foo.trycloudflare.com:443",
        "https://foo.trycloudflare.com/mcp",
        "https://u:p@foo.trycloudflare.com",
        "https://foo.trycloudflare.com?x=y",
        "https://foo.trycloudflare.com#x",
        "https://foo.bar.trycloudflare.com",
        "https://foo.trycloudflare.com\n",
    ],
)
def test_quick_origin_restrictions(origin):
    assert not accepted_origin(origin, {"mode": "quick"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "192.168.1.1", "::1", "fc00::1"]
)
async def test_probe_rejects_nonpublic_dns(monkeypatch, ip):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))],
    )
    with pytest.raises(ProviderError, match="invalid_preview_origin"):
        await probe("https://test.trycloudflare.com", {"mode": "quick"})


@pytest.mark.asyncio
async def test_github_repo_token_is_exact_repo_and_permissions():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    requests = []
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()

    def handler(r):
        requests.append(r)
        return httpx.Response(201, json={"token": "fictional-result"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        p = Providers(Config(github_app_id="123", github_private_key=pem), http)
        assert (
            await p.repository_token({"installation_id": 456, "repo_id": 789}) == "fictional-result"
        )
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body == {
        "repository_ids": [789],
        "permissions": {"contents": "read", "actions": "write", "secrets": "write"},
    }
    assert requests[0].url == "https://api.github.com/app/installations/456/access_tokens"


@pytest.mark.asyncio
async def test_provider_500_write_is_unknown_not_retry():
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(503, json={"sensitive": "fictional-canary"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        p = Providers(Config(), http)
        with pytest.raises(ProviderError) as error:
            await p.gh("POST", "/fake", token="fictional-token", body={"test": True})
    assert error.value.uncertain is True and "canary" not in str(error.value) and len(calls) == 1


@pytest.mark.asyncio
async def test_oidc_signature_issuer_audience_and_expiration():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk["kid"] = "test-key"
    urls = []

    def handler(r):
        urls.append(str(r.url))
        return httpx.Response(200, json={"keys": [jwk]})

    now = int(time.time())
    valid = {
        "iss": "https://token.actions.githubusercontent.com",
        "aud": "https://setup.example",
        "sub": "repo:owner/name:ref:refs/heads/main",
        "iat": now,
        "nbf": now - 1,
        "exp": now + 60,
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        p = Providers(Config(origin="https://setup.example"), http)

        def token(claims):
            return jwt.encode(
                claims,
                private,
                algorithm="RS256",
                headers={"kid": "test-key", "jku": "https://attacker.test/keys"},
            )

        assert (await p.oidc_claims(token(valid)))["aud"] == "https://setup.example"
        bads = [
            dict(valid, iss="https://attacker.test"),
            dict(valid, aud="other"),
            dict(valid, exp=now - 1),
            dict(valid, nbf=now + 30),
        ]
        bads += [
            {k: v for k, v in valid.items() if k != missing}
            for missing in ["exp", "iat", "nbf", "iss", "aud", "sub"]
        ]
        for bad in bads:
            with pytest.raises(ProviderError, match="invalid_run_identity"):
                await p.oidc_claims(token(bad))
        unsigned = jwt.encode(valid, key="", algorithm="none", headers={"kid": "test-key"})
        with pytest.raises(ProviderError, match="invalid_run_identity"):
            await p.oidc_claims(unsigned)
    assert set(urls) == {"https://token.actions.githubusercontent.com/.well-known/jwks"}


OAUTH_ORIGIN = "https://setup.example"


class OAuthProvider(MockProviders):
    async def exchange(self, provider, code, verifier):
        self.calls.append(("exchange", provider, code, verifier))
        return "fictional-oauth-token"


@pytest.mark.asyncio
async def test_oauth_session_provider_pkce_one_use_and_cookie_rotation():
    p = OAuthProvider()
    app = create_app(
        Config(
            origin=OAUTH_ORIGIN,
            database=":memory:",
            github_app_id="123",
            github_slug="test-app",
            github_client_id="test-client",
            github_client_secret="fictional-secret",
            github_private_key="fictional-key-not-used",
            template_sha="a" * 40,
        ),
        providers=p,
    )
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url=OAUTH_ORIGIN) as c,
        httpx.AsyncClient(transport=transport, base_url=OAUTH_ORIGIN) as attacker,
    ):
        r = await c.get("/api/session")
        csrf = r.json()["csrf"]
        old_sid = c.cookies["__Host-bridge_initializer"]
        headers = {"Origin": OAUTH_ORIGIN, "X-CSRF-Token": csrf}
        r = await c.post("/api/auth/github", headers=headers, json={})
        assert r.status_code == 200
        query = parse_qs(urlsplit(r.json()["redirect"]).query)
        state = query["state"][0]
        pending = app.state.sessions[old_sid]["oauth"]
        assert query["code_challenge_method"] == ["S256"] and query["code_challenge"] == [
            oauth_challenge(pending["verifier"])
        ]
        await attacker.get("/api/session")
        r = await attacker.get(
            "/auth/github/callback", params={"code": "test-code", "state": state}
        )
        assert r.status_code == 409
        assert not p.calls
        r = await c.get("/auth/github/callback", params={"code": "test-code", "state": state})
        assert r.status_code == 303
        cookie = r.headers["set-cookie"]
        assert all(x in cookie for x in ["Secure", "HttpOnly", "SameSite=lax", "Path=/"])
        assert (
            c.cookies["__Host-bridge_initializer"] != old_sid and old_sid not in app.state.sessions
        )
        assert len(p.calls) == 1 and p.calls[0][:3] == ("exchange", "github", "test-code")
        r = await c.get("/auth/github/callback", params={"code": "test-code", "state": state})
        assert r.status_code == 409
        assert len(p.calls) == 1
        current = (await c.get("/api/session")).json()
        assert current["installed"] and current["user"]["id"] == 42 and current["csrf"] != csrf


@pytest.mark.asyncio
async def test_unconfigured_live_has_no_mutation_or_mock_fallback():
    p = OAuthProvider()
    app = create_app(Config(database=":memory:"), providers=p)
    origin = "http://127.0.0.1:8765"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=origin) as c:
        r = await c.get("/api/session")
        s = r.json()
        assert s["mode"] == "live" and s["github_ready"] is False
        h = {"Origin": origin, "X-CSRF-Token": s["csrf"]}
        r = await c.post("/api/auth/github", headers=h, json={})
        assert r.status_code == 409 and r.json()["code"] == "not_configured"
        assert p.calls == []
        assert (
            await c.post("/api/mock/scenario", headers=h, json={"scenario": "success"})
        ).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "none",
        "inactive",
        "account",
        "zone",
        "dns-name",
        "dns-type",
        "dns-target",
        "not-proxied",
        "ingress-host",
        "ingress-origin",
        "ingress-path",
        "catchall",
        "extra-ingress",
    ],
)
async def test_named_tunnel_binding_is_reverified(change):
    op = {
        "zone_id": "zone-1",
        "zone_name": "example.com",
        "account_id": "account-1",
        "dns_id": "dns-1",
        "hostname": "desktop-abcdef.example.com",
        "tunnel_id": "tunnel-1",
    }
    zone = {"status": "active", "name": "example.com", "account": {"id": "account-1"}}
    record = {
        "type": "CNAME",
        "name": op["hostname"],
        "content": "tunnel-1.cfargotunnel.com",
        "proxied": True,
    }
    rules = [
        {"hostname": op["hostname"], "service": "http://127.0.0.1:8080"},
        {"service": "http_status:404"},
    ]
    if change == "inactive":
        zone["status"] = "pending"
    if change == "account":
        zone["account"]["id"] = "foreign-account"
    if change == "zone":
        zone["name"] = "foreign.test"
    if change == "dns-name":
        record["name"] = "foreign.example.com"
    if change == "dns-type":
        record["type"] = "A"
    if change == "dns-target":
        record["content"] = "attacker.cfargotunnel.com"
    if change == "not-proxied":
        record["proxied"] = False
    if change == "ingress-host":
        rules[0]["hostname"] = "foreign.example.com"
    if change == "ingress-origin":
        rules[0]["service"] = "http://169.254.169.254"
    if change == "ingress-path":
        rules[0]["path"] = "/private"
    if change == "catchall":
        rules[1]["service"] = "http://127.0.0.1:22"
    if change == "extra-ingress":
        rules.append({"service": "http_status:404"})

    def handler(request):
        assert request.method == "GET"
        if request.url.path.endswith("/dns_records/dns-1"):
            result = record
        elif request.url.path.endswith("/configurations"):
            result = {"config": {"ingress": rules}}
        else:
            result = zone
        return httpx.Response(200, json={"success": True, "result": result})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = Providers(Config(), http)
        if change == "none":
            await provider.verify_tunnel(op, "fictional-cf-token")
        else:
            with pytest.raises(ProviderError):
                await provider.verify_tunnel(op, "fictional-cf-token")


@pytest.mark.asyncio
async def test_named_launch_refuses_changed_binding_before_secret_or_dispatch():
    provider = RunProvider()

    async def changed(op, token):
        raise ProviderError("tunnel_binding_changed")

    provider.verify_tunnel = changed
    app, c, headers = await fixture_run(provider)
    sid = c.cookies["bridge_initializer_unconfigured"]
    app.state.sessions[sid]["cloudflare_token"] = "fictional-cf-token"
    op = app.state.store.get("op1")
    op.update(stage="awaiting_password", mode="named", key_id="key-1")
    app.state.store.save(op)
    async with aclosing(c):
        response = await c.post(
            "/api/operations/op1/start",
            headers=headers,
            json={
                "saved_password": True,
                "key_id": "key-1",
                "encrypted_value": base64.b64encode(b"x" * 91).decode(),
            },
        )
        assert response.status_code == 200
        await asyncio.sleep(0.02)
        current = app.state.store.get("op1")
        assert current["stage"] == "failed" and current["error"] == "tunnel_binding_changed"
        assert provider.calls == []


def test_runner_report_has_only_oidc_and_nonsecret_readiness(capsys):
    from scripts.initializer_events import report

    requests = []
    env = {
        "INITIALIZER_OPERATION": "a" * 32,
        "INITIALIZER_LAUNCH_ID": "b" * 32,
        "INITIALIZER_URL": "https://setup.example",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://fixture.actions.githubusercontent.com/idtoken?x=y",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "fictional-runtime-token",
        "BRIDGE_OWNER_TOKEN": "fictional-owner-canary",
        "TUNNEL_TOKEN": "fictional-tunnel-canary",
    }

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"value": "fictional-oidc-assertion"})
        return httpx.Response(200, json={"accepted": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert report(
            "ready",
            1,
            origin="https://fixture.trycloudflare.com",
            expires_at=123,
            env=env,
            client=http,
        )
    assert len(requests) == 2
    assert requests[0].headers["authorization"] == "Bearer fictional-runtime-token"
    assert requests[1].headers["authorization"] == "Bearer fictional-oidc-assertion"
    assert "audience=https%3A%2F%2Fsetup.example" in str(requests[0].url)
    payload = json.loads(requests[1].content)
    assert set(payload) == {
        "event",
        "sequence",
        "origin",
        "expires_at",
        "smoke_passed",
        "launch_id",
    }
    assert payload["launch_id"] == "b" * 32
    serialized = (
        str(requests[1].url) + str(dict(requests[1].headers)) + requests[1].content.decode()
    )
    assert all(
        env[key] not in serialized
        for key in ["BRIDGE_OWNER_TOKEN", "TUNNEL_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"]
    )
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["none", "wrong-app", "wrong-owner", "suspended", "secrets-read", "actions-read"]
)
async def test_installation_requires_personal_owner_and_expected_permissions(change):
    installation = {
        "id": 101,
        "app_id": 123,
        "account": {"id": 42},
        "suspended_at": None,
        "permissions": {"contents": "read", "actions": "write", "secrets": "write"},
    }
    if change == "wrong-app":
        installation["app_id"] = 999
    if change == "wrong-owner":
        installation["account"]["id"] = 999
    if change == "suspended":
        installation["suspended_at"] = "2026-01-01T00:00:00Z"
    if change == "secrets-read":
        installation["permissions"]["secrets"] = "read"
    if change == "actions-read":
        installation["permissions"]["actions"] = "read"

    def handler(request):
        return httpx.Response(200, json={"installations": [installation]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = Providers(Config(github_app_id="123"), http)
        if change == "none":
            assert await provider.installation("fictional-token", {"id": 42}) == 101
        else:
            with pytest.raises(ProviderError):
                await provider.installation("fictional-token", {"id": 42})


def test_restart_revokes_short_lived_auth_and_does_not_claim_dcr_persistence():
    import hashlib

    from desktop_bridge.auth import Auth
    from desktop_bridge.state import BridgeError

    owner = "fictional-test-owner-not-for-deployment"
    first = Auth(owner, "https://fixture.example")
    client_data = first.register({"redirect_uris": ["https://client.example/callback"]})
    verifier = "x" * 43
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    request = {
        "client_id": client_data["client_id"],
        "redirect_uri": client_data["redirect_uris"][0],
        "response_type": "code",
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    exchange = {
        "grant_type": "authorization_code",
        "client_id": request["client_id"],
        "redirect_uri": request["redirect_uri"],
        "code_verifier": verifier,
    }
    bearer = first.exchange({**exchange, "code": first.approve(request)})["access_token"]
    pending_code = first.approve(request)
    sid, _ = first.login(owner)
    second = Auth(owner, "https://fixture.example")
    assert not second.bearer(bearer) and second.session(sid) is None
    assert client_data["client_id"] not in second.clients
    with pytest.raises(BridgeError):
        second.exchange({**exchange, "code": pending_code})


@pytest.mark.asyncio
async def test_unknown_restart_cannot_adopt_a_recent_older_run():
    from datetime import datetime, timezone

    now = time.time()
    op = {
        "id": "operation-1",
        "owner_login": "preview-user",
        "repo_name": "desktop-bridge-test",
        "commit": "a" * 40,
        "previous_run_id": 988,
        "dispatch_nonce": "b" * 32,
        "dispatch_at": now,
        "created_at": now - 20,
    }
    # Run 989 may have been accepted but is not yet visible in the list. Run 987
    # is older than the immediately previous run and inside the 30-second window.
    old = {
        "id": 987,
        "html_url": "https://github.com/fixture/repo/actions/runs/987",
        "display_title": "MCP preview operation-1",
        "head_sha": "a" * 40,
        "event": "workflow_dispatch",
        "run_attempt": 1,
        "created_at": datetime.fromtimestamp(now - 10, timezone.utc).isoformat(),
    }

    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"workflow_runs": [old]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = Providers(Config(), http)
        assert await provider.find_dispatched_run(op, "fictional-token") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("nonce", [None, "wrong-nonce", "b" * 32])
async def test_ready_is_bound_to_current_launch_nonce(nonce):
    provider = RunProvider()

    async def ready_probe(origin, op):
        pass

    app, c, _ = await fixture_run(provider, probe=ready_probe)
    op = app.state.store.get("op1")
    op["dispatch_nonce"] = "b" * 32
    app.state.store.save(op)
    body = {
        "event": "ready",
        "sequence": 1,
        "origin": "https://fixture.trycloudflare.com",
        "smoke_passed": True,
        "expires_at": time.time() + 3000,
    }
    if nonce is not None:
        body["launch_id"] = nonce
    async with aclosing(c):
        response = await c.post(
            "/api/run-events/op1", headers={"Authorization": "Bearer fictional-test"}, json=body
        )
        if nonce == "b" * 32:
            assert response.status_code == 200
            assert app.state.store.get("op1")["stage"] == "ready"
        else:
            assert response.status_code == 409
            assert response.json()["code"] == "invalid_run_identity"
            assert app.state.store.get("op1")["stage"] == "waiting_ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 2])
async def test_unknown_dispatch_requires_exact_unique_nonce_match(count):
    from datetime import datetime, timezone

    now = time.time()
    op = {
        "id": "operation-1",
        "owner_login": "preview-user",
        "repo_name": "desktop-bridge-test",
        "commit": "a" * 40,
        "dispatch_nonce": "b" * 32,
        "dispatch_at": now,
        "created_at": now - 20,
    }
    runs = [
        {
            "id": 1000 + n,
            "html_url": f"https://github.com/fixture/repo/actions/runs/{1000 + n}",
            "display_title": "MCP preview operation-1 " + "b" * 32,
            "head_sha": "a" * 40,
            "event": "workflow_dispatch",
            "run_attempt": 1,
            "created_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        }
        for n in range(count)
    ]

    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"workflow_runs": runs})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await Providers(Config(), http).find_dispatched_run(op, "fictional-token")
        if count == 1:
            assert result["run_id"] == 1000
        else:
            assert result is None
