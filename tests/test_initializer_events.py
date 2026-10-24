import importlib.util
import json
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location(
    "initializer_events", Path(__file__).parents[1] / "scripts/initializer_events.py"
)
events = importlib.util.module_from_spec(spec)
spec.loader.exec_module(events)


def env():
    return {
        "INITIALIZER_URL": "https://setup.example.test",
        "INITIALIZER_OPERATION": "a" * 32,
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/token?api=1",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "test-runtime-token",
        "BRIDGE_OWNER_TOKEN": "never-transmit-owner-canary",
        "TUNNEL_TOKEN": "never-transmit-tunnel-canary",
    }


def test_disabled_report_makes_no_request():
    def handler(request):
        pytest.fail("No request is allowed when reporting is disabled")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert not events.report("ready", 1, env={}, client=client)


def test_report_oidc_is_audience_bound_and_contains_no_owner_secrets():
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            assert request.url.host == "pipelines.actions.githubusercontent.com"
            assert request.url.params["audience"] == env()["INITIALIZER_URL"]
            assert request.headers["authorization"] == "Bearer test-runtime-token"
            return httpx.Response(200, json={"value": "test-audience-bound-assertion"})
        assert request.url == "https://setup.example.test/api/run-events/" + "a" * 32
        assert request.headers["authorization"] == "Bearer test-audience-bound-assertion"
        assert json.loads(request.content) == {
            "event": "ready",
            "sequence": 1,
            "origin": "https://fictional.trycloudflare.com",
            "expires_at": 123,
            "smoke_passed": True,
        }
        return httpx.Response(200, json={"accepted": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert events.report(
            "ready",
            1,
            origin="https://fictional.trycloudflare.com",
            expires_at=123,
            env=env(),
            client=client,
        )
    output = b"".join(r.content for r in seen)
    assert b"never-transmit" not in output
    assert b"runtime-token" not in seen[-1].content


@pytest.mark.parametrize(
    "value",
    [
        "https://attacker.test/token",
        "http://pipelines.actions.githubusercontent.com/token",
        "https://pipelines.actions.githubusercontent.com.attacker.test/token",
        "https://user:secret@pipelines.actions.githubusercontent.com/token",
        "https://pipelines.actions.githubusercontent.com:8080/token",
    ],
)
def test_runtime_request_token_only_goes_to_github(value):
    with pytest.raises(ValueError):
        events.oidc_request_url(value, "https://setup.example.test")


@pytest.mark.parametrize(
    "url",
    [
        "http://setup.example.test",
        "https://setup.example.test/callback",
        "https://setup.example.test?secret=1",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://example.local",
        "https://user:secret@setup.example.test",
    ],
)
def test_initializer_origin_rejected(url):
    with pytest.raises(ValueError):
        events.report_config({**env(), "INITIALIZER_URL": url})


def test_report_network_error_does_not_disclose_response_or_tokens(capsys):
    def handler(request):
        raise httpx.ConnectError("private diagnostic containing never-transmit", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert not events.report("ready", 1, env=env(), client=client)
    assert capsys.readouterr() == ("", "")
