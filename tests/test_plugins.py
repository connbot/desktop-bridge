import asyncio
import json
import logging
from contextlib import AsyncExitStack

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError

from desktop_bridge.app import Runtime, create_app
from desktop_bridge.plugins import (
    MCPPlugins,
    PluginConfig,
    ServerConfig,
    load_config,
    namespaced,
    nested_schema,
    redact,
)
from desktop_bridge.state import BridgeError

TOKEN = "test-only-owner-token-not-for-deployment-123"
SECRET = "synthetic-secret-only-never-a-real-key"


def config(id="demo", **overrides):
    return ServerConfig.model_validate({
        "id": id, "enabled": True, "url": f"https://{id}.example/mcp",
        "allowed_tools": ["echo"], **overrides,
    })


def synthetic_server(name="test"):
    server = FastMCP(name, stateless_http=True, json_response=True,
                     transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    calls = []

    @server.tool(annotations=types.ToolAnnotations(readOnlyHint=True))
    async def echo(value: str, bridge_action_id: str = "upstream-default") -> dict[str, str]:
        calls.append((value, bridge_action_id))
        return {"value": value, "bridge_action_id": bridge_action_id}

    @server.tool()
    async def forbidden() -> str:
        raise AssertionError("Non-allowlisted tool must never be called")

    app = server.streamable_http_app()
    return server, app, calls


class FakeBrowser:
    async def connect(self):
        pass

    async def close(self):
        pass


class FakeCoding(FakeBrowser):
    tools = []


def runtime_with_plugins(tmp_path, plugins):
    return Runtime(
        tmp_path, desktop=object(), browser=FakeBrowser(), coding=FakeCoding(), plugins=plugins
    )


async def test_no_config_disabled_and_missing_credentials_make_no_connections(monkeypatch):
    def unexpected(cfg):
        pytest.fail("Unconfigured service must not connect")
    monkeypatch.delenv("TEST_PLUGIN_TOKEN", raising=False)
    registry = MCPPlugins(PluginConfig(servers=[
        config("disabled", enabled=False),
        config("empty", allowed_tools=[]),
        config("missing", headers=[{"name": "x-api-key", "value_env": "TEST_PLUGIN_TOKEN"}]),
    ]), transport_factory=unexpected)
    await registry.start()
    assert registry.tools() == []
    assert [s["state"] for s in registry.status()["servers"]] == [
        "disabled", "no_tools_allowed", "unconfigured"
    ]
    await registry.close()
    empty = MCPPlugins()
    await empty.start()
    assert empty.status()["servers"] == []
    assert empty.status()["configuration_reload"] == "restart_required"
    await empty.close()


@pytest.mark.parametrize("url", [
    "http://example.com/mcp", "https://localhost/mcp", "https://127.0.0.1/mcp",
    "https://[::1]/mcp", "https://10.0.0.1/mcp", "https://169.254.169.254/",
    "https://example.internal/mcp", "https://example.local/mcp", "https://example.com:8443/mcp",
    "https://user:password@example.com/mcp", "https://example.com/mcp?key=secret",
    "https://example.com/mcp#token", "https://example.com\n/mcp", "https://example.com./mcp",
    "https://metadata/mcp", "https://éxample.com/mcp",
])
def test_endpoints_fail_closed(url):
    with pytest.raises(ValidationError):
        config(url=url)


@pytest.mark.parametrize("override", [
    {"allowed_tools": ["*"]}, {"allowed_tools": ["echo", "echo"]},
    {"allowed_tools": ["../tool"]}, {"transport": "stdio"},
    {"command": "sh"}, {"token": "do-not-accept-inline"},
    {"headers": [{"name": "Host", "value_env": "TEST_TOKEN"}]},
    {"headers": [{"name": "Authorization", "value_env": "BRIDGE_OWNER_TOKEN"}]},
    {"headers": [{"name": "Authorization", "value_env": "DATABASE_URL"}]},
    {"headers": [{"name": "x-key", "value_env": "TEST_TOKEN", "prefix": "Bearer\n"}]},
    {"headers": [{"name": "x-key", "value_env": "TEST_TOKEN"},
                 {"name": "X-Key", "value_env": "TEST_TOKEN"}]},
    {"call_timeout_seconds": 1000}, {"connect_timeout_seconds": 0},
])
def test_invalid_server_configuration_rejected(override):
    with pytest.raises(ValidationError):
        config(**override)


def test_duplicate_server_names_rejected():
    with pytest.raises(ValidationError):
        PluginConfig(servers=[config(), config()])


def test_config_file_is_admin_owned_not_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = tmp_path / "plugins.json"
    path.write_text('{"version":1,"servers":[]}')
    assert load_config(path, workspace).servers == []
    path.chmod(0o666)
    with pytest.raises(BridgeError, match="owner-managed"):
        load_config(path, workspace)
    nested = workspace / "plugins.json"
    nested.write_text('{"servers":[]}')
    with pytest.raises(BridgeError):
        load_config(nested, workspace)
    assert load_config(None, workspace).servers == []


def test_invalid_config_error_does_not_echo_content(tmp_path):
    path = tmp_path / "plugins.json"
    path.write_text('{"secret":"never-log-me", "servers":"bad"}')
    with pytest.raises(BridgeError) as error:
        load_config(path, tmp_path / "workspace")
    assert "never-log-me" not in str(error.value)
    assert str(path) not in str(error.value)


def test_namespace_is_stable_unique_and_bounded():
    assert namespaced("mail", "echo") == "mcp_mail__echo"
    assert namespaced("one", "echo") != namespaced("two", "echo")
    a, b = namespaced("mail", "a" * 90), namespaced("mail", "a" * 89 + "b")
    assert a != b and len(a) <= 64 and len(b) <= 64


def test_nested_schema_local_refs_and_rejected_remote_refs():
    schema = {"type": "object", "properties": {"item": {"$ref": "#/$defs/Thing"}},
              "$defs": {"Thing": {"type": "string"}}}
    assert nested_schema(schema)["properties"]["item"]["$ref"] == (
        "#/properties/arguments/$defs/Thing"
    )
    assert schema["properties"]["item"]["$ref"] == "#/$defs/Thing"
    for ref in ["https://remote.example/schema", "file:///private/key", "#anchor"]:
        with pytest.raises(ValueError):
            nested_schema({"$ref": ref})


def test_argument_envelope_cannot_collide_with_upstream_arguments():
    outer_id, arguments = MCPPlugins.unpack({
        "bridge_action_id": "outer", "arguments": {"bridge_action_id": "inner", "arguments": 1}
    })
    assert outer_id == "outer" and arguments == {"bridge_action_id": "inner", "arguments": 1}
    for invalid in [{}, {"bridge_action_id": "outer", "arguments": {}, "url": "bad"},
                    {"bridge_action_id": "", "arguments": {}},
                    {"bridge_action_id": "a", "arguments": []}]:
        with pytest.raises(BridgeError):
            MCPPlugins.unpack(invalid)
    with pytest.raises(BridgeError):
        MCPPlugins.unpack({"bridge_action_id": "a", "arguments": {"x": "a" * 70000}})


async def test_real_mcp_handshake_namespacing_allowlist_and_call():
    first, first_app, first_calls = synthetic_server("first")
    second, second_app, second_calls = synthetic_server("second")
    apps = {"first": first_app, "second": second_app}
    registry = MCPPlugins(PluginConfig(servers=[config("first"), config("second")]),
                          transport_factory=lambda cfg: httpx.ASGITransport(app=apps[cfg.id]))
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(first.session_manager.run())
        await stack.enter_async_context(second.session_manager.run())
        try:
            await registry.start()
            assert [s["state"] for s in registry.status()["servers"]] == ["ready", "ready"]
            tools = registry.tools()
            assert {t.name for t in tools} == {"mcp_first__echo", "mcp_second__echo"}
            assert all(t.annotations is None for t in tools)  # Ignore remote readOnlyHint.
            assert set(tools[0].inputSchema["properties"]) == {"arguments", "bridge_action_id"}
            result = await registry.call("mcp_first__echo", {"value": "hello", "bridge_action_id": "inner"})
            assert result.structuredContent == {"value": "hello", "bridge_action_id": "inner"}
            assert result.isError is False and result.content
            assert first_calls == [("hello", "inner")] and second_calls == []
            with pytest.raises(BridgeError, match="Unknown or disallowed"):
                await registry.call("mcp_first__forbidden", {})
        finally:
            await registry.close()


async def test_bad_server_does_not_break_other_service():
    good, app, calls = synthetic_server("good")
    def transport(cfg):
        if cfg.id == "good":
            return httpx.ASGITransport(app=app)
        def fail(request):
            raise httpx.ConnectError("must-not-leak-secret-network-error")
        return httpx.MockTransport(fail)
    registry = MCPPlugins(PluginConfig(servers=[config("bad"), config("good")]),
                          transport_factory=transport)
    async with good.session_manager.run():
        try:
            await registry.start()
            states = {s["id"]: s["state"] for s in registry.status()["servers"]}
            assert states == {"bad": "unavailable", "good": "ready"}
            assert "must-not-leak" not in json.dumps(registry.status())
            assert (await registry.call("mcp_good__echo", {"value": "ok"})).isError is False
            assert len(calls) == 1
        finally:
            await registry.close()


async def test_plugin_calls_obey_lease_and_receipts_even_read_only_hint(tmp_path):
    server, app, calls = synthetic_server()
    registry = MCPPlugins(PluginConfig(servers=[config()]),
                          transport_factory=lambda cfg: httpx.ASGITransport(app=app))
    runtime = runtime_with_plugins(tmp_path, registry)
    args = {"bridge_action_id": "once", "arguments": {"value": "hello"}}
    async with server.session_manager.run():
        try:
            await runtime.start()
            with pytest.raises(BridgeError, match="Agent cannot act"):
                await runtime.call("mcp_demo__echo", args)
            await runtime.call("session_start", {})
            first = await runtime.call("mcp_demo__echo", args)
            second = await runtime.call("mcp_demo__echo", args)
            assert first.model_dump() == second.model_dump()
            assert len(calls) == 1
            with pytest.raises(BridgeError) as error:
                await runtime.call("mcp_demo__echo", {**args, "arguments": {"value": "changed"}})
            assert error.value.code == "IDEMPOTENCY_CONFLICT"
            await runtime.control("private")
            with pytest.raises(BridgeError, match="Agent cannot act"):
                await runtime.call("mcp_demo__echo", {**args, "bridge_action_id": "blocked"})
            assert len(calls) == 1
        finally:
            await runtime.close()


async def test_timeout_is_unknown_outcome_never_replayed(tmp_path):
    server = FastMCP("slow", stateless_http=True, json_response=True,
                     transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    calls = []
    @server.tool()
    async def slow() -> str:
        calls.append("started")
        await asyncio.sleep(3)
        return "done"
    app = server.streamable_http_app()
    registry = MCPPlugins(PluginConfig(servers=[config(allowed_tools=["slow"], call_timeout_seconds=1)]),
                          transport_factory=lambda cfg: httpx.ASGITransport(app=app))
    runtime = runtime_with_plugins(tmp_path, registry)
    args = {"bridge_action_id": "slow-once", "arguments": {}}
    async with server.session_manager.run():
        try:
            await runtime.start()
            await runtime.call("session_start", {})
            with pytest.raises(BridgeError) as error:
                await runtime.call("mcp_demo__slow", args)
            assert error.value.code == "PLUGIN_OUTCOME_UNKNOWN"
            with pytest.raises(BridgeError) as error:
                await runtime.call("mcp_demo__slow", args)
            assert error.value.code == "OUTCOME_UNKNOWN"
            assert calls == ["started"]
            assert registry.status()["servers"][0]["state"] == "unavailable"
        finally:
            await runtime.close()


async def test_header_credentials_are_redacted_from_result_and_receipt(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("TEST_PLUGIN_TOKEN", SECRET)
    server, app, _ = synthetic_server()
    registry = MCPPlugins(PluginConfig(servers=[config(headers=[
        {"name": "Authorization", "value_env": "TEST_PLUGIN_TOKEN", "prefix": "Bearer "}
    ])]), transport_factory=lambda cfg: httpx.ASGITransport(app=app))
    runtime = runtime_with_plugins(tmp_path, registry)
    caplog.set_level(logging.DEBUG)
    async with server.session_manager.run():
        try:
            await runtime.start()
            await runtime.call("session_start", {})
            result = await runtime.call("mcp_demo__echo", {
                "bridge_action_id": "redaction", "arguments": {"value": SECRET}
            })
            assert SECRET not in json.dumps(result.model_dump(mode="json"))
            assert "[REDACTED]" in json.dumps(result.model_dump(mode="json"))
            row = runtime.session.db.execute("SELECT result FROM receipts WHERE id='redaction'").fetchone()
            assert SECRET not in row[0]
            assert SECRET not in json.dumps(registry.status())
            # The in-process server's own logs are outside the bridge's control.
            bridge_logs = [r.getMessage() for r in caplog.records
                           if r.name == "mcp.client.streamable_http"]
            assert bridge_logs == []
        finally:
            await runtime.close()
    assert redact({"api_key": "unknown-secret", "value": SECRET}, (SECRET,)) == {
        "api_key": "[REDACTED]", "value": "[REDACTED]"
    }


def test_plugin_status_is_authenticated_and_cannot_register_servers(tmp_path):
    runtime = runtime_with_plugins(tmp_path, MCPPlugins())
    app = create_app(tmp_path, TOKEN, "http://testserver", runtime)
    with TestClient(app) as client:
        assert client.get("/api/plugins").status_code == 401
        assert client.post("/api/login", json={"token": TOKEN}).status_code == 200
        response = client.get("/api/plugins")
        assert response.status_code == 200
        assert response.json()["servers"] == []
        assert response.headers["cache-control"] == "no-store"
        assert client.post("/api/plugins", json={"url": "https://evil.example"}).status_code == 405


def rpc_transport(tools=None, result=None, *, tool_failure=False):
    calls, requests = [], []
    tools = tools if tools is not None else [
        {"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}
    ]
    def handle(request):
        requests.append(request)
        if request.method != "POST":
            return httpx.Response(405)
        body = json.loads(request.content)
        if "id" not in body:
            return httpx.Response(202)
        method = body["method"]
        if method == "initialize":
            value = {"protocolVersion": body["params"]["protocolVersion"],
                     "capabilities": {"tools": {}}, "serverInfo": {"name": "synthetic", "version": "1"}}
        elif method == "tools/list":
            value = {"tools": tools}
        elif method == "tools/call":
            calls.append(body["params"])
            if tool_failure:
                raise httpx.ConnectError("private-upstream-error-must-not-leak")
            value = result if result is not None else {
                "content": [{"type": "text", "text": "ok"}], "isError": False
            }
        else:
            raise AssertionError("Unexpected request")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": value})
    return httpx.MockTransport(handle), calls, requests


async def test_structured_content_and_error_flag_preserved_and_headers_scoped(monkeypatch):
    monkeypatch.setenv("TEST_PLUGIN_TOKEN", SECRET)
    transport, calls, requests = rpc_transport(result={
        "content": [{"type": "text", "text": "Tool refused the request"}],
        "structuredContent": {"reason": "refused"}, "isError": True,
    })
    registry = MCPPlugins(PluginConfig(servers=[config(headers=[
        {"name": "x-api-key", "value_env": "TEST_PLUGIN_TOKEN"}
    ])]), transport_factory=lambda cfg: transport)
    try:
        await registry.start()
        assert registry.status()["servers"][0]["state"] == "ready"
        result = await registry.call("mcp_demo__echo", {"x": 1})
        assert result.isError is True
        assert result.structuredContent == {"reason": "refused"}
        assert result.content[0].text == "Tool refused the request"
        assert calls == [{"name": "echo", "arguments": {"x": 1}}]
        assert all(request.headers["x-api-key"] == SECRET for request in requests)
    finally:
        await registry.close()


@pytest.mark.parametrize("tools", [
    [{"name": "echo", "inputSchema": {"type": "object"}}] * 2,
    [{"name": "echo", "inputSchema": {"type": "array"}}],
    [{"name": "echo", "inputSchema": {"type": "object", "$ref": "https://private.example/schema"}}],
    [{"name": "echo", "inputSchema": {"type": "object", "description": "x" * 40000}}],
])
async def test_bad_catalog_is_unavailable_not_partially_exposed(tools):
    transport, _, _ = rpc_transport(tools=tools)
    registry = MCPPlugins(PluginConfig(servers=[config()]), transport_factory=lambda cfg: transport)
    try:
        await registry.start()
        assert registry.tools() == []
        assert registry.status()["servers"][0]["state"] == "unavailable"
    finally:
        await registry.close()


async def test_outage_during_call_does_not_repeat_request_or_leak_error(tmp_path):
    transport, calls, _ = rpc_transport(tool_failure=True)
    registry = MCPPlugins(PluginConfig(servers=[config()]), transport_factory=lambda cfg: transport)
    runtime = runtime_with_plugins(tmp_path, registry)
    try:
        await runtime.start()
        await runtime.call("session_start", {})
        args = {"bridge_action_id": "outage", "arguments": {}}
        with pytest.raises(BridgeError) as error:
            await runtime.call("mcp_demo__echo", args)
        assert error.value.code == "PLUGIN_OUTCOME_UNKNOWN"
        assert "private-upstream" not in str(error.value)
        with pytest.raises(BridgeError) as error:
            await runtime.call("mcp_demo__echo", args)
        assert error.value.code == "OUTCOME_UNKNOWN"
        assert len(calls) == 1
        assert registry.tools() == []
    finally:
        await runtime.close()


async def test_redirect_cannot_forward_credentials_to_another_host(monkeypatch):
    monkeypatch.setenv("TEST_PLUGIN_TOKEN", SECRET)
    requested = []
    def redirect(request):
        requested.append(str(request.url))
        return httpx.Response(307, headers={"Location": "http://169.254.169.254/private"})
    registry = MCPPlugins(PluginConfig(servers=[config(headers=[
        {"name": "Authorization", "value_env": "TEST_PLUGIN_TOKEN", "prefix": "Bearer "}
    ])]), transport_factory=lambda cfg: httpx.MockTransport(redirect))
    try:
        await registry.start()
        assert registry.status()["servers"][0]["state"] == "unavailable"
        assert requested == ["https://demo.example/mcp"]
    finally:
        await registry.close()


async def test_large_result_fails_without_receipt_success_or_replay(tmp_path):
    transport, calls, _ = rpc_transport(result={
        "content": [{"type": "text", "text": "x" * 300000}], "isError": False,
    })
    registry = MCPPlugins(PluginConfig(servers=[config()]), transport_factory=lambda cfg: transport)
    runtime = runtime_with_plugins(tmp_path, registry)
    try:
        await runtime.start()
        await runtime.call("session_start", {})
        args = {"bridge_action_id": "large", "arguments": {}}
        with pytest.raises(BridgeError):
            await runtime.call("mcp_demo__echo", args)
        with pytest.raises(BridgeError) as error:
            await runtime.call("mcp_demo__echo", args)
        assert error.value.code == "OUTCOME_UNKNOWN"
        assert len(calls) == 1
    finally:
        await runtime.close()


def test_sdk_payload_logging_suppressed_but_application_logging_works(caplog):
    caplog.set_level(logging.WARNING)
    root = logging.getLogger()
    root.handle(logging.LogRecord("root", logging.WARNING, "/library/mcp/shared/session.py", 1,
                                  SECRET, (), None))
    logging.warning("safe application diagnostic")
    assert SECRET not in caplog.text
    assert "safe application diagnostic" in caplog.text


async def test_target_or_credential_change_conflicts_with_old_action_id(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_PLUGIN_TOKEN", "synthetic-account-one")
    first_transport, first_calls, _ = rpc_transport()
    cfg = PluginConfig(servers=[config(headers=[
        {"name": "x-api-key", "value_env": "TEST_PLUGIN_TOKEN"}
    ])])
    first = MCPPlugins(cfg, transport_factory=lambda cfg: first_transport)
    runtime = runtime_with_plugins(tmp_path, first)
    try:
        await runtime.start()
        await runtime.call("session_start", {})
        args = {"bridge_action_id": "old-action", "arguments": {}}
        await runtime.call("mcp_demo__echo", args)
        old_binding = first.target_identity("mcp_demo__echo")
    finally:
        await runtime.close()
    monkeypatch.setenv("TEST_PLUGIN_TOKEN", "synthetic-account-two")
    second_transport, second_calls, _ = rpc_transport()
    second = MCPPlugins(cfg, transport_factory=lambda cfg: second_transport)
    runtime = runtime_with_plugins(tmp_path, second)
    try:
        await runtime.start()
        await runtime.call("session_start", {})
        assert second.target_identity("mcp_demo__echo") != old_binding
        with pytest.raises(BridgeError) as error:
            await runtime.call("mcp_demo__echo", args)
        assert error.value.code == "IDEMPOTENCY_CONFLICT"
        assert len(first_calls) == 1 and second_calls == []
    finally:
        await runtime.close()


async def test_in_flight_result_is_hidden_if_private_takeover_occurs(tmp_path):
    server = FastMCP("delayed", stateless_http=True, json_response=True,
                     transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    started, finish = asyncio.Event(), asyncio.Event()
    @server.tool()
    async def delayed() -> str:
        started.set()
        await finish.wait()
        return "private upstream result"
    app = server.streamable_http_app()
    registry = MCPPlugins(PluginConfig(servers=[config(allowed_tools=["delayed"])]),
                          transport_factory=lambda cfg: httpx.ASGITransport(app=app))
    runtime = runtime_with_plugins(tmp_path, registry)
    async with server.session_manager.run():
        try:
            await runtime.start()
            await runtime.call("session_start", {})
            call = asyncio.create_task(runtime.call("mcp_demo__delayed", {
                "bridge_action_id": "takeover", "arguments": {}
            }))
            await started.wait()
            takeover = asyncio.create_task(runtime.control("private"))
            await asyncio.sleep(0)
            finish.set()
            with pytest.raises(BridgeError) as error:
                await call
            assert error.value.code == "PRIVATE_TAKEOVER"
            await takeover
            receipt = runtime.session.db.execute(
                "SELECT state,result FROM receipts WHERE id='takeover'"
            ).fetchone()
            assert receipt == ("unknown", None)
        finally:
            await runtime.close()
