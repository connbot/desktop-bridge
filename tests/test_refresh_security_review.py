"""Independent negative coverage for renewable OAuth grants, using fake credentials."""

import base64
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from desktop_bridge import auth as auth_module
from desktop_bridge.auth import Auth
from desktop_bridge.state import BridgeError

OWNER = "security-review-fixture-owner-not-for-deployment"
ORIGIN = "https://refresh-review.example"
DAY = 24 * 3600


@pytest.fixture
def clock(monkeypatch):
    current = [1_900_000_000.0]
    monkeypatch.setattr(
        auth_module, "time", SimpleNamespace(time=lambda: current[0], monotonic=time.monotonic)
    )
    return current


@pytest.fixture
def auth(clock):
    return Auth(OWNER, ORIGIN)


def issue(auth):
    client = auth.register({
        "redirect_uris": ["https://client.example/callback"],
        "grant_types": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_method": "none",
    })
    verifier = "s" * 43
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    params = {
        "client_id": client["client_id"],
        "redirect_uri": client["redirect_uris"][0],
        "response_type": "code",
        "code_challenge_method": "S256",
        "code_challenge": challenge.decode().rstrip("="),
        "resource": ORIGIN + "/mcp",
        "scope": "computer",
    }
    code = auth.approve(params)
    tokens = auth.exchange({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client["client_id"],
        "redirect_uri": params["redirect_uri"],
        "code_verifier": verifier,
        "resource": params["resource"],
    })
    return client["client_id"], tokens


def refresh(auth, issued_client_id, token, **changes):
    return auth.exchange({
        "grant_type": "refresh_token",
        "client_id": issued_client_id,
        "refresh_token": token,
        "resource": ORIGIN + "/mcp",
        **changes,
    })


def test_refresh_cannot_be_used_as_access_or_owner_login(auth):
    client_id, tokens = issue(auth)
    assert not auth.bearer(tokens["refresh_token"])
    assert not auth.bearer(OWNER)
    with pytest.raises(BridgeError):
        auth.login(tokens["refresh_token"])
    with pytest.raises(BridgeError):
        refresh(auth, client_id, tokens["access_token"])
    assert auth.bearer(tokens["access_token"])
    assert refresh(auth, client_id, tokens["refresh_token"])["refresh_token"]


@pytest.mark.parametrize("changes", [
    {"client_id": "unregistered-client"},
    {"client_id": ""},
    {"resource": "https://other.example/mcp"},
    {"scope": "computer admin"},
])
def test_invalid_refresh_binding_does_not_consume_valid_token(auth, changes):
    client_id, tokens = issue(auth)
    with pytest.raises(BridgeError):
        refresh(auth, client_id, tokens["refresh_token"], **changes)
    assert auth.bearer(tokens["access_token"])
    replacement = refresh(auth, client_id, tokens["refresh_token"])
    assert auth.bearer(replacement["access_token"])


def test_foreign_client_cannot_replay_to_revoke_another_family(auth):
    client_a, tokens_a = issue(auth)
    client_b, tokens_b = issue(auth)
    next_a = refresh(auth, client_a, tokens_a["refresh_token"])
    with pytest.raises(BridgeError):
        refresh(auth, client_b, tokens_a["refresh_token"])
    assert auth.bearer(next_a["access_token"])
    assert auth.bearer(tokens_b["access_token"])
    assert refresh(auth, client_a, next_a["refresh_token"])


def test_replay_revokes_all_descendants_without_touching_other_family(auth):
    client_a, first_a = issue(auth)
    client_b, first_b = issue(auth)
    second_a = refresh(auth, client_a, first_a["refresh_token"])
    third_a = refresh(auth, client_a, second_a["refresh_token"])
    with pytest.raises(BridgeError):
        refresh(auth, client_a, first_a["refresh_token"])
    for tokens in (first_a, second_a, third_a):
        assert not auth.bearer(tokens["access_token"])
        with pytest.raises(BridgeError):
            refresh(auth, client_a, tokens["refresh_token"])
    assert auth.bearer(first_b["access_token"])
    assert refresh(auth, client_b, first_b["refresh_token"])


def test_old_replay_marker_survives_multiple_idle_windows(auth, clock):
    client_id, first = issue(auth)
    latest = first
    for _ in range(3):
        clock[0] += 6 * DAY
        latest = refresh(auth, client_id, latest["refresh_token"])
    with pytest.raises(BridgeError):
        refresh(auth, client_id, first["refresh_token"])
    assert not auth.bearer(latest["access_token"])
    with pytest.raises(BridgeError):
        refresh(auth, client_id, latest["refresh_token"])


def test_absolute_deadline_caps_final_access_and_refresh(auth, clock):
    start = clock[0]
    client_id, latest = issue(auth)
    for day in (6, 12, 18, 24, 29):
        clock[0] = start + day * DAY
        latest = refresh(auth, client_id, latest["refresh_token"])
    clock[0] = start + 30 * DAY - 15
    latest = refresh(auth, client_id, latest["refresh_token"])
    assert 0 < latest["expires_in"] <= 15
    assert auth.bearer(latest["access_token"])
    clock[0] = start + 30 * DAY
    assert not auth.bearer(latest["access_token"])
    with pytest.raises(BridgeError):
        refresh(auth, client_id, latest["refresh_token"])


def test_revocation_and_new_process_never_restore_renewable_grant(auth):
    client_id, tokens = issue(auth)
    sid, csrf = auth.login(OWNER)
    auth.revoke()
    assert not auth.session(sid)
    with pytest.raises(BridgeError):
        auth.check_csrf(sid, csrf)
    assert not auth.bearer(tokens["access_token"])
    with pytest.raises(BridgeError):
        refresh(auth, client_id, tokens["refresh_token"])
    for owner in (OWNER, "rotated-security-review-fixture-owner-token"):
        restarted = Auth(owner, ORIGIN)
        assert not restarted.clients
        assert not restarted.bearer(tokens["access_token"])
        with pytest.raises(BridgeError):
            refresh(restarted, client_id, tokens["refresh_token"])


def test_duplicate_concurrent_refresh_cannot_create_two_live_successors(auth):
    client_id, tokens = issue(auth)

    def attempt():
        try:
            return refresh(auth, client_id, tokens["refresh_token"])
        except BridgeError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, BridgeError)]
    assert len(successes) == len(failures) == 1
    assert failures[0].code == "INVALID_GRANT"
    # Strict replay protection also revokes the winner if the old token is retried.
    assert not auth.bearer(successes[0]["access_token"])
    with pytest.raises(BridgeError):
        refresh(auth, client_id, successes[0]["refresh_token"])


def test_no_plaintext_bearer_credentials_are_retained_in_auth_state(auth):
    client_id, first = issue(auth)
    second = refresh(auth, client_id, first["refresh_token"])
    retained = repr(vars(auth))
    for tokens in (first, second):
        assert tokens["access_token"] not in retained
        assert tokens["refresh_token"] not in retained
    assert OWNER not in retained


@pytest.mark.parametrize("at_boundary", [False, True])
def test_idle_deadline_is_exact_and_expired_state_is_removed(auth, clock, at_boundary):
    client_id, tokens = issue(auth)
    clock[0] += 7 * DAY - (0 if at_boundary else 1)
    if at_boundary:
        with pytest.raises(BridgeError) as rejected:
            refresh(auth, client_id, tokens["refresh_token"])
        assert rejected.value.code == "INVALID_GRANT"
        assert not auth.grants
        assert not auth.tokens
        assert not auth.refresh_tokens
    else:
        replacement = refresh(auth, client_id, tokens["refresh_token"])
        clock[0] += 2
        assert auth.bearer(replacement["access_token"])
        assert refresh(auth, client_id, replacement["refresh_token"])


def test_refresh_storage_limit_fails_closed_without_consuming_token(auth, monkeypatch):
    monkeypatch.setattr(auth_module, "MAX_REFRESH_TOKENS", 1)
    client_id, tokens = issue(auth)
    before_grants = repr(auth.grants)
    with pytest.raises(BridgeError) as rejected:
        refresh(auth, client_id, tokens["refresh_token"])
    assert rejected.value.code == "RATE_LIMITED"
    assert repr(auth.grants) == before_grants
    assert len(auth.refresh_tokens) == 1
    assert auth.bearer(tokens["access_token"])
    monkeypatch.setattr(auth_module, "MAX_REFRESH_TOKENS", 2)
    replacement = refresh(auth, client_id, tokens["refresh_token"])
    assert replacement["refresh_token"] != tokens["refresh_token"]
    assert auth.bearer(replacement["access_token"])
