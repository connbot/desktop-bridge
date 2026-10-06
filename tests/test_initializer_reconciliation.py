"""Read-only recovery of an uncertain dispatch must never submit a second run."""

import time

import httpx
import pytest

from initializer.app import create_app
from initializer.config import Config
from initializer.mock import MockProviders
from initializer.providers import Providers


@pytest.mark.asyncio
async def test_unknown_dispatch_can_adopt_only_a_unique_verified_run():
    class Recovery(MockProviders):
        async def find_dispatched_run(self, op, token):
            return {"run_id": 812, "run_url": "https://github.com/test/example/actions/runs/812"}

    provider = Recovery()
    app = create_app(Config(mode="mock", database=":memory:"), providers=provider)
    now = time.time()
    app.state.store.save(
        {
            "id": "op",
            "owner_id": 42,
            "owner_login": "preview-user",
            "installation_id": 101,
            "repo_id": 123,
            "repo_name": "desktop-bridge-aaaaaaaaaaaa",
            "stage": "unknown",
            "failed_stage": "launching",
            "created_at": now,
            "dispatch_at": now,
            "error": "dispatch_result_unknown",
            "uncertain": True,
            "mode": "quick",
            "commit": "a" * 40,
            "last_sequence": 0,
        }
    )
    provider.runs[812] = {
        "id": 812,
        "status": "in_progress",
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "event": "workflow_dispatch",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765"
    ) as client:
        session = (await client.get("/api/session")).json()
        headers = {"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": session["csrf"]}
        await client.post("/api/auth/github", headers=headers)
        response = await client.get("/api/operations/op")
        assert response.json()["stage"] == "waiting_ready"
        assert response.json()["run_id"] == 812
        assert "origin" not in response.json()
        assert not provider.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 2])
async def test_run_name_sha_attempt_and_creation_time_control_reconciliation(count):
    now = time.time()
    op = {
        "id": "deployment",
        "owner_login": "person",
        "repo_name": "desktop-bridge",
        "commit": "a" * 40,
        "created_at": now - 100,
        "dispatch_at": now - 10,
        "dispatch_nonce": "unique-launch",
        "previous_run_id": 1,
    }
    candidate = {
        "id": 2,
        "display_title": "MCP preview deployment unique-launch",
        "head_sha": "a" * 40,
        "event": "workflow_dispatch",
        "run_attempt": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "html_url": "https://github.com/person/desktop-bridge/actions/runs/2",
    }
    runs = [{**candidate, "id": 2 + i} for i in range(count)]
    # Older, wrong-version and rerun candidates cannot create an ambiguous match.
    runs += [
        {**candidate, "id": 1},
        {**candidate, "id": 7, "display_title": "MCP preview deployment old-launch"},
        {**candidate, "head_sha": "b" * 40},
        {**candidate, "run_attempt": 2},
        {**candidate, "created_at": "2000-01-01T00:00:00Z"},
    ]

    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"workflow_runs": runs})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await Providers(Config(), client=client).find_dispatched_run(op, "fixture-token")
    assert (result is not None) is (count == 1)
