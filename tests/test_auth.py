import base64
import hashlib

import pytest

from desktop_bridge.auth import Auth
from desktop_bridge.state import BridgeError


@pytest.fixture
def auth():
    return Auth("test-only-owner-token-not-for-deployment-123", "https://bridge.example")


def grant(auth):
    client = auth.register({"redirect_uris": ["http://127.0.0.1:1234/callback"]})
    verifier = "x" * 43
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    params = {
        "client_id": client["client_id"],
        "redirect_uri": client["redirect_uris"][0],
        "response_type": "code",
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    code = auth.approve(params)
    return {
        "grant_type": "authorization_code",
        "client_id": client["client_id"],
        "code": code,
        "redirect_uri": params["redirect_uri"],
        "code_verifier": verifier,
    }


def test_code_is_bound_single_use_and_revoked(auth):
    data = grant(auth)
    token = auth.exchange(data)["access_token"]
    assert auth.bearer(token)
    assert not auth.bearer("test-only-owner-token-not-for-deployment-123")
    with pytest.raises(BridgeError):
        auth.exchange(data)
    auth.revoke()
    assert not auth.bearer(token)


@pytest.mark.parametrize(
    "field,value",
    [
        ("code_verifier", "wrong" * 10),
        ("client_id", "other"),
        ("redirect_uri", "https://evil.test"),
        ("resource", "https://other.test/mcp"),
    ],
)
def test_grant_binding(auth, field, value):
    data = grant(auth)
    data[field] = value
    with pytest.raises(BridgeError):
        auth.exchange(data)


@pytest.mark.parametrize(
    "uri",
    [
        "http://evil.test/callback",
        "javascript:alert(1)",
        "https://a.test/#token",
        "https://u:p@a.test/",
        "file:///tmp/x",
    ],
)
def test_redirect_restrictions(auth, uri):
    with pytest.raises(BridgeError):
        auth.register({"redirect_uris": [uri]})


def test_owner_sessions_csrf(auth):
    with pytest.raises(BridgeError):
        auth.login("bad")
    sid, csrf = auth.login("test-only-owner-token-not-for-deployment-123")
    auth.check_csrf(sid, csrf)
    with pytest.raises(BridgeError):
        auth.check_csrf(sid, "bad")
    auth.revoke()
    assert auth.session(sid) is None


def test_throttles(auth):
    for _ in range(20):
        auth.throttle("login")
    with pytest.raises(BridgeError):
        auth.throttle("login")
