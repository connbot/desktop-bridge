"""Provider contracts use fictional credentials and a local-only HTTP transport."""

import json
from datetime import datetime, timezone

import httpx
import pytest

from initializer.config import Config
from initializer.providers import ProviderError, Providers

TEMPLATE = "template-owner/reviewed-template"
TEMPLATE_SHA = "a" * 40
TREE_SHA = "b" * 40
HEAD_SHA = "c" * 40
REPO_TOKEN = "fictional-generated-repo-token"
USER_TOKEN = "fictional-user-token"
REPO_PATH = "/repos/example-owner/desktop-bridge-example"
TEMPLATE_PATH = f"/repos/{TEMPLATE}/commits/{TEMPLATE_SHA}"
WORKFLOW_PATH = REPO_PATH + "/actions/workflows/preview.yml"
RUN_URL = "https://github.com/example-owner/desktop-bridge-example/actions/runs/123"


def operation():
    return {
        "id": "fictional-operation",
        "owner_login": "example-owner",
        "owner_id": 42,
        "repo_name": "desktop-bridge-example",
        "repo_id": 101,
        "branch": "main",
        "mode": "quick",
        "commit": HEAD_SHA,
        "dispatch_nonce": "fictional-launch",
        "created_at": 1_700_000_000,
        "dispatch_at": 1_700_000_100,
    }


def verification_response(request, *, template_status=200, tree=TREE_SHA):
    assert request.method == "GET"
    assert request.url.host == "api.github.com"
    path = request.url.path
    if path == TEMPLATE_PATH:
        assert request.headers["Authorization"] == "Bearer " + USER_TOKEN
        return httpx.Response(template_status, json={"commit": {"tree": {"sha": tree}}})
    assert request.headers["Authorization"] == "Bearer " + REPO_TOKEN
    if path == REPO_PATH:
        return httpx.Response(200, json={"id": 101, "owner": {"id": 42}})
    if path == REPO_PATH + "/commits/main":
        return httpx.Response(200, json={"sha": HEAD_SHA, "commit": {"tree": {"sha": TREE_SHA}}})
    assert path == WORKFLOW_PATH
    return httpx.Response(200, json={"state": "active"})


@pytest.mark.asyncio
async def test_template_read_uses_user_token_without_widening_repository_token_scope():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return verification_response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = Providers(Config(template=TEMPLATE, template_sha=TEMPLATE_SHA), http)
        assert await provider.verify_repository(operation(), REPO_TOKEN, USER_TOKEN) == HEAD_SHA
    assert calls == [REPO_PATH, REPO_PATH + "/commits/main", TEMPLATE_PATH, WORKFLOW_PATH]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, code", [(401, "authorization_expired"), (403, "permission_denied")]
)
async def test_template_read_rejection_never_falls_back_to_anonymous_or_installation_token(
    status, code
):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return verification_response(request, template_status=status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = Providers(Config(template=TEMPLATE, template_sha=TEMPLATE_SHA), http)
        with pytest.raises(ProviderError) as error:
            await provider.verify_repository(operation(), REPO_TOKEN, USER_TOKEN)
    assert error.value.code == code
    assert error.value.uncertain is False
    assert calls == [REPO_PATH, REPO_PATH + "/commits/main", TEMPLATE_PATH]


@pytest.mark.asyncio
async def test_template_tree_mismatch_still_blocks_workflow_access():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return verification_response(request, tree="d" * 40)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = Providers(Config(template=TEMPLATE, template_sha=TEMPLATE_SHA), http)
        with pytest.raises(ProviderError, match="repository_version_changed"):
            await provider.verify_repository(operation(), REPO_TOKEN, USER_TOKEN)
    assert calls == [REPO_PATH, REPO_PATH + "/commits/main", TEMPLATE_PATH]


@pytest.mark.asyncio
@pytest.mark.parametrize("user_token", [None, ""])
async def test_missing_user_token_fails_before_any_anonymous_request(user_token):
    def handler(request):
        pytest.fail("Missing user authorization must not issue an HTTP request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = Providers(Config(template=TEMPLATE, template_sha=TEMPLATE_SHA), http)
        with pytest.raises(ProviderError, match="authorization_expired"):
            await provider.verify_repository(operation(), REPO_TOKEN, user_token)


@pytest.mark.asyncio
async def test_dispatch_uses_2026_api_run_details_contract():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer " + REPO_TOKEN
        assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
        if request.method == "PUT":
            assert request.url.path == WORKFLOW_PATH + "/enable"
            return httpx.Response(204)
        assert request.method == "POST"
        assert request.url.path == WORKFLOW_PATH + "/dispatches"
        # 2026-03-10 removed this old opt-in; run details are now unconditional.
        # https://docs.github.com/en/rest/about-the-rest-api/breaking-changes#version-2026-03-10
        assert json.loads(request.content) == {
            "ref": "main",
            "inputs": {
                "mode": "preview",
                "tunnel": "quick",
                "minutes": "60",
                "public_url": "",
                "operation_id": "fictional-operation",
                "launch_id": "fictional-launch",
                "initializer_url": "https://setup.example",
            },
        }
        return httpx.Response(
            200,
            json={
                "workflow_run_id": 123,
                "run_url": "https://api.github.com" + REPO_PATH + "/actions/runs/123",
                "html_url": RUN_URL,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = Providers(Config(origin="https://setup.example"), http)
        assert await provider.dispatch(operation(), REPO_TOKEN) == {
            "run_id": 123,
            "run_url": RUN_URL,
        }
    assert calls == [("PUT", WORKFLOW_PATH + "/enable"), ("POST", WORKFLOW_PATH + "/dispatches")]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["empty", "missing_id", "timeout", "server_error"])
async def test_unknown_dispatch_is_reconciled_read_only_without_redispatch(outcome):
    calls = []
    op = operation()

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "PUT":
            assert request.url.path == WORKFLOW_PATH + "/enable"
            return httpx.Response(204)
        if request.method == "POST":
            assert request.url.path == WORKFLOW_PATH + "/dispatches"
            if outcome == "timeout":
                raise httpx.ReadTimeout("Fictional timeout", request=request)
            if outcome == "server_error":
                return httpx.Response(503)
            if outcome == "missing_id":
                return httpx.Response(200, json={"html_url": RUN_URL})
            return httpx.Response(204)
        assert request.method == "GET"
        assert request.url.path == WORKFLOW_PATH + "/runs"
        assert dict(request.url.params) == {"event": "workflow_dispatch", "per_page": "100"}
        return httpx.Response(
            200,
            json={
                "workflow_runs": [
                    {
                        "id": 123,
                        "html_url": RUN_URL,
                        "display_title": "MCP preview " + op["id"] + " " + op["dispatch_nonce"],
                        "head_sha": HEAD_SHA,
                        "event": "workflow_dispatch",
                        "run_attempt": 1,
                        "created_at": datetime.fromtimestamp(
                            op["dispatch_at"], timezone.utc
                        ).isoformat(),
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = Providers(Config(), http)
        with pytest.raises(ProviderError) as error:
            await provider.dispatch(op, REPO_TOKEN)
        assert error.value.uncertain is True
        expected = (
            "provider_unavailable"
            if outcome in {"timeout", "server_error"}
            else "dispatch_result_unknown"
        )
        assert error.value.code == expected
        assert await provider.find_dispatched_run(op, REPO_TOKEN) == {
            "run_id": 123,
            "run_url": RUN_URL,
        }
    assert calls == [
        ("PUT", WORKFLOW_PATH + "/enable"),
        ("POST", WORKFLOW_PATH + "/dispatches"),
        ("GET", WORKFLOW_PATH + "/runs"),
    ]
