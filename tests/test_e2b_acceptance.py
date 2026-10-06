"""MOCKED contract/security tests only; these never attest to live E2B isolation."""

import importlib.util
import json
import shlex
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from websockets.exceptions import InvalidMessage, InvalidStatus
from websockets.http11 import Response

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("e2b_acceptance", ROOT / "scripts/e2b_acceptance.py")
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)

BASE = "https://8080-fictional-sandbox.e2b.dev"
OWNER = "fictional-owner-token-only-for-mocked-tests"
KEY = "fictional-api-key-only-for-mocked-tests"
RUN_ID = "a" * 32


@pytest.fixture(autouse=True)
def prevent_real_network(monkeypatch):
    # Each test must explicitly install mocks for every network boundary it uses.
    def forbidden(*args, **kwargs):
        raise AssertionError("A mocked unit test attempted an unmocked live boundary")

    monkeypatch.setattr(acceptance, "load_dependencies", forbidden)
    monkeypatch.setattr(acceptance.httpx, "Client", forbidden)
    monkeypatch.setattr(acceptance, "websocket_connect", forbidden)
    monkeypatch.delenv("E2B_API_KEY", raising=False)


@pytest.fixture
def state():
    return {
        "version": 1, "phase": "ready", "sandbox_id": "fictional-sandbox",
        "template": "fictional-template", "public_url": BASE, "owner_token": OWNER,
    }


def sandbox():
    return SimpleNamespace(
        sandbox_id="fictional-sandbox",
        get_host=lambda port: f"{port}-fictional-sandbox.e2b.dev",
        commands=SimpleNamespace(run=Mock(return_value=SimpleNamespace(
            exit_code=0, stdout='{"ok": true}',
        ))),
    )


def mocked_dependencies(state):
    initial, resumed = sandbox(), sandbox()
    sdk = SimpleNamespace(connect=Mock(side_effect=[initial, resumed]), pause=Mock(return_value=True))
    return SimpleNamespace(
        Sandbox=sdk, public_origin=Mock(return_value=BASE), wait_ready=Mock(),
        read_state=Mock(return_value=state), smoke=Mock(),
    ), initial, resumed


def ws_status(status, body=b"provider gateway failure"):
    return InvalidStatus(Response(status, "mock response", httpx.Headers(), body))


def mock_ingress(monkeypatch, *, aux_status=502, envd_status=401, viewer_status=403):
    requests = []

    def get(url):
        requests.append(("GET", url, {}))
        if url == BASE + "/healthz":
            return httpx.Response(200, json={"ready": True})
        if url == BASE + "/api/status":
            return httpx.Response(401)
        return httpx.Response(aux_status, text="mock provider gateway failure")

    def post(url, **kwargs):
        requests.append(("POST", url, kwargs))
        return httpx.Response(envd_status)

    client = SimpleNamespace(get=Mock(side_effect=get), post=Mock(side_effect=post))
    factory = Mock(return_value=nullcontext(client))
    monkeypatch.setattr(acceptance.httpx, "Client", factory)

    def websocket(url, **kwargs):
        requests.append(("WS", url, kwargs))
        raise ws_status(viewer_status if url.endswith("/desktop/view") else aux_status)

    monkeypatch.setattr(acceptance, "websocket_connect", websocket)
    return requests, factory


def test_mock_cli_requires_explicit_live_flag_even_with_key(monkeypatch, capsys):
    monkeypatch.setenv("E2B_API_KEY", KEY)
    with pytest.raises(SystemExit) as failure:
        acceptance.main([])
    assert failure.value.code == 2
    output = capsys.readouterr().err
    assert "--run-live" in output and KEY not in output


def test_mock_cli_requires_key_before_loading_state_or_sdk(capsys):
    with pytest.raises(SystemExit) as failure:
        acceptance.main(["--run-live"])
    assert failure.value.code == 2
    assert "E2B_API_KEY is required" in capsys.readouterr().err


@pytest.mark.parametrize("changes", [
    {"phase": "created"}, {"version": 2}, {"template": ""}, {"owner_token": ""},
    {"public_url": "http://8080-fictional-sandbox.e2b.dev"},
    {"public_url": BASE + "/mcp"}, {"public_url": BASE + "/"},
    {"public_url": BASE + "?token=fictional"}, {"public_url": BASE + "#fragment"},
    {"public_url": "https://username:password@fictional.e2b.dev"},
    {"public_url": BASE + ":8080"}, {"public_url": BASE + "\n"},
])
def test_mock_cli_invalid_state_cannot_reach_provider(state, changes, monkeypatch, capsys):
    state.update(changes)
    deps, _, _ = mocked_dependencies(state)
    monkeypatch.setattr(acceptance, "load_dependencies", lambda: deps)
    monkeypatch.setenv("E2B_API_KEY", KEY)
    assert acceptance.main(["--run-live"]) == 1
    deps.Sandbox.connect.assert_not_called()
    output = capsys.readouterr().err
    assert KEY not in output and OWNER not in output


@pytest.mark.parametrize("option,value", [("--sandbox-timeout", "1"), ("--probe-timeout", "31")])
def test_mock_cli_rejects_unbounded_options(monkeypatch, option, value):
    monkeypatch.setenv("E2B_API_KEY", KEY)
    with pytest.raises(SystemExit):
        acceptance.main(["--run-live", option, value])


def test_mock_lifecycle_reconnects_same_id_and_checks_each_phase(state, monkeypatch, capsys):
    deps, initial, resumed = mocked_dependencies(state)
    isolation, fixture = Mock(), Mock()
    monkeypatch.setattr(acceptance, "check_isolation", isolation)
    monkeypatch.setattr(acceptance, "profile_fixture", fixture)
    monkeypatch.setattr(acceptance, "load_dependencies", lambda: deps)
    monkeypatch.setenv("E2B_API_KEY", KEY)
    assert acceptance.main(["--run-live"]) == 0
    deps.Sandbox.pause.assert_called_once_with("fictional-sandbox", keep_memory=True)
    assert deps.Sandbox.connect.call_count == 2
    for call in deps.Sandbox.connect.call_args_list:
        assert call.args == ("fictional-sandbox",)
        assert call.kwargs == {"timeout": 600}
    assert [call.args[0] for call in isolation.call_args_list] == [initial, resumed]
    assert [call.args[:2] for call in fixture.call_args_list] == [
        (initial, "write"), (resumed, "verify"), (resumed, "cleanup"),
    ]
    assert deps.wait_ready.call_count == 2
    assert deps.smoke.call_count == 2
    receipt_ids = [call.kwargs["action_id"] for call in deps.smoke.call_args_list]
    assert receipt_ids[0] != receipt_ids[1]
    for call in deps.smoke.call_args_list:
        assert call.args == (BASE, OWNER)
    assert state["phase"] == "ready"  # No mutation of the launcher's private state.
    output = capsys.readouterr().out
    assert "PASS live E2B" in output and OWNER not in output and KEY not in output
    for guest in (initial, resumed):
        assert guest.commands.run.call_count == 1
        assert guest.commands.run.call_args.args[0].startswith("/usr/bin/python3 -I -S -c ")


@pytest.mark.parametrize("phase", ["initial", "resumed"])
def test_mock_origin_mismatch_withholds_owner_token(state, monkeypatch, phase):
    deps, _, _ = mocked_dependencies(state)
    deps.public_origin.side_effect = ["https://wrong.example"] if phase == "initial" else [
        BASE, "https://wrong.example",
    ]
    monkeypatch.setattr(acceptance, "check_isolation", Mock())
    monkeypatch.setattr(acceptance, "profile_fixture", Mock())
    with pytest.raises(acceptance.AcceptanceFailure, match="owner token withheld"):
        acceptance.run_live(state, deps)
    assert deps.smoke.call_count == (0 if phase == "initial" else 1)


def test_mock_changed_sandbox_id_fails_before_auth(state, monkeypatch):
    deps, initial, _ = mocked_dependencies(state)
    initial.sandbox_id = "wrong-sandbox"
    with pytest.raises(acceptance.AcceptanceFailure, match="different sandbox"):
        acceptance.run_live(state, deps)
    deps.smoke.assert_not_called()


def test_mock_pause_failure_never_creates_or_deletes_a_sandbox(state, monkeypatch):
    deps, _, _ = mocked_dependencies(state)
    deps.Sandbox.pause.return_value = False
    monkeypatch.setattr(acceptance, "check_isolation", Mock())
    monkeypatch.setattr(acceptance, "profile_fixture", Mock())
    with pytest.raises(acceptance.AcceptanceFailure, match="confirm a new"):
        acceptance.run_live(state, deps)
    assert deps.Sandbox.connect.call_count == 1
    # The mock deliberately exposes no create/kill/delete methods at all.


def test_mock_provider_failure_cannot_disclose_credentials(state, monkeypatch, capsys):
    deps, _, _ = mocked_dependencies(state)
    deps.Sandbox.connect.side_effect = RuntimeError(f"{KEY} {OWNER} Cookie: private-session")
    monkeypatch.setattr(acceptance, "load_dependencies", lambda: deps)
    monkeypatch.setenv("E2B_API_KEY", KEY)
    assert acceptance.main(["--run-live"]) == 1
    output = capsys.readouterr().err
    assert "connecting" in output
    assert KEY not in output and OWNER not in output and "private-session" not in output


def test_mock_state_read_failure_cannot_disclose_private_contents(state, monkeypatch, capsys):
    deps, _, _ = mocked_dependencies(state)
    deps.read_state.side_effect = ValueError(OWNER)
    monkeypatch.setattr(acceptance, "load_dependencies", lambda: deps)
    monkeypatch.setenv("E2B_API_KEY", KEY)
    assert acceptance.main(["--run-live"]) == 1
    assert OWNER not in capsys.readouterr().err
    deps.Sandbox.connect.assert_not_called()


def test_mock_native_ports_and_envd_are_probed_without_credentials(monkeypatch):
    requests, factory = mock_ingress(monkeypatch)
    acceptance.probe_ingress(sandbox(), BASE)
    expected = {f"https://{port}-fictional-sandbox.e2b.dev/" for port in acceptance.BLOCKED_PORTS}
    assert expected <= {url for method, url, _ in requests if method == "GET"}
    assert {url.replace("https://", "wss://") for url in expected} <= {
        url for method, url, _ in requests if method == "WS"
    }
    rpc = next(item for item in requests if item[0] == "POST")
    assert rpc[1].endswith("49983-fictional-sandbox.e2b.dev/process.Process/List")
    assert rpc[2] == {"json": {}, "headers": {"Connect-Protocol-Version": "1"}}
    assert all("Cookie" not in str(kwargs) and "Authorization" not in str(kwargs)
               and "X-Access-Token" not in str(kwargs) for _, _, kwargs in requests)
    factory.assert_called_once_with(timeout=5, follow_redirects=False, trust_env=False)


@pytest.mark.parametrize("status", [200, 101, 301, 401, 403, 404, 500])
def test_mock_auxiliary_http_responses_are_not_misreported_as_blocked(monkeypatch, status):
    mock_ingress(monkeypatch, aux_status=status)
    with pytest.raises(acceptance.AcceptanceFailure, match="Port 5900 HTTP"):
        acceptance.probe_ingress(sandbox(), BASE)


@pytest.mark.parametrize("status", [200, 404, 502, 503, 504])
def test_mock_envd_requires_explicit_unauthenticated_rejection(monkeypatch, status):
    mock_ingress(monkeypatch, envd_status=status)
    with pytest.raises(acceptance.AcceptanceFailure, match="envd"):
        acceptance.probe_ingress(sandbox(), BASE)


@pytest.mark.parametrize("body", [b"RFB 003.008", b'{"webSocketDebuggerUrl":"ws://private"}',
                                   b"/devtools/browser/private", b"synthetic-e2b-canary-aaaa"])
def test_mock_gateway_status_does_not_hide_underlying_protocol(body):
    with pytest.raises(acceptance.AcceptanceFailure):
        acceptance.check_blocked_response(502, body, 9222, "HTTP")


def test_mock_open_auxiliary_websocket_is_failure(monkeypatch):
    mock_ingress(monkeypatch)
    monkeypatch.setattr(acceptance, "websocket_connect", lambda *args, **kwargs: nullcontext())
    with pytest.raises(acceptance.AcceptanceFailure, match="accepted a public WebSocket"):
        acceptance.probe_ingress(sandbox(), BASE)


@pytest.mark.parametrize("error", [InvalidMessage("RFB 003.008"), OSError("DNS failed")])
def test_mock_ambiguous_transport_failure_is_not_a_pass(monkeypatch, error):
    mock_ingress(monkeypatch)
    monkeypatch.setattr(acceptance, "websocket_connect", Mock(side_effect=error))
    with pytest.raises(type(error)):
        acceptance.probe_ingress(sandbox(), BASE)


def test_mock_timeouts_require_live_positive_control(monkeypatch):
    calls = []

    def get(url):
        calls.append(url)
        if url == BASE + "/healthz":
            return httpx.Response(200)
        if url == BASE + "/api/status":
            return httpx.Response(401)
        raise httpx.ReadTimeout("mock timeout")

    client = SimpleNamespace(get=get, post=lambda *args, **kwargs: httpx.Response(401))
    monkeypatch.setattr(acceptance.httpx, "Client", lambda **kwargs: nullcontext(client))

    def websocket(url, **kwargs):
        if url.endswith("/desktop/view"):
            raise ws_status(403)
        raise TimeoutError("mock timeout")

    monkeypatch.setattr(acceptance, "websocket_connect", websocket)
    acceptance.probe_ingress(sandbox(), BASE)
    assert calls.count(BASE + "/healthz") == 2


@pytest.mark.parametrize("status", [404, 502])
def test_mock_viewer_needs_auth_rejection_not_gateway_failure(monkeypatch, status):
    mock_ingress(monkeypatch, viewer_status=status)
    with pytest.raises(acceptance.AcceptanceFailure, match="viewer"):
        acceptance.probe_ingress(sandbox(), BASE)


def test_mock_firewall_checks_exact_live_rules_and_both_family_counters():
    guest = sandbox()
    guest.commands.run.side_effect = [
        SimpleNamespace(exit_code=0),
        SimpleNamespace(exit_code=0, stdout="[4:240] -A BRIDGE_E2B_INPUT -p tcp -j DROP\n"),
        SimpleNamespace(exit_code=0, stdout="[0:0] -A BRIDGE_E2B_INPUT -p tcp -j DROP\n"),
    ]
    assert acceptance.firewall_counters(guest) == (4, 0)
    assert guest.commands.run.call_args_list[0].args == (acceptance.FIREWALL_CHECK,)
    assert "iptables-legacy-save" in guest.commands.run.call_args_list[1].args[0]
    assert "ip6tables-legacy-save" in guest.commands.run.call_args_list[2].args[0]
    assert all(call.kwargs["user"] == "root" for call in guest.commands.run.call_args_list)
    assert all(call.kwargs["envs"] == {
        "HOME": "/root", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "BASH_ENV": "/dev/null", "ENV": "/dev/null",
    } for call in guest.commands.run.call_args_list)


@pytest.mark.parametrize("counter", ["", "-A BRIDGE_E2B_INPUT -p tcp -j ACCEPT",
                                      "[1:10] -A wrong -p tcp -j DROP"])
def test_mock_absent_firewall_counter_fails_closed(counter):
    guest = sandbox()
    guest.commands.run.side_effect = [SimpleNamespace(exit_code=0),
                                     SimpleNamespace(exit_code=0, stdout=counter)]
    with pytest.raises(acceptance.AcceptanceFailure, match="counter"):
        acceptance.firewall_counters(guest)


@pytest.mark.parametrize("after,passed", [((1, 0), True), ((0, 1), True), ((0, 0), False)])
def test_mock_live_canary_requires_counter_increment_and_always_stops_process(
    monkeypatch, after, passed,
):
    guest, handle = sandbox(), SimpleNamespace(kill=Mock())
    guest.commands.run.side_effect = [handle, SimpleNamespace(exit_code=0)]
    monkeypatch.setattr(acceptance, "firewall_counters", Mock(side_effect=[(0, 0), (0, 0), after]))
    ingress, blocked = Mock(), Mock()
    monkeypatch.setattr(acceptance, "probe_ingress", ingress)
    monkeypatch.setattr(acceptance, "probe_blocked_port", blocked)
    monkeypatch.setattr(acceptance.httpx, "Client", lambda **kwargs: nullcontext("mock-http"))
    if passed:
        acceptance.check_isolation(guest, BASE, 5, RUN_ID)
    else:
        with pytest.raises(acceptance.AcceptanceFailure, match="increment"):
            acceptance.check_isolation(guest, BASE, 5, RUN_ID)
    blocked.assert_called_once_with("mock-http", guest, BASE, acceptance.CANARY_PORT, 5)
    handle.kill.assert_called_once_with()
    start = guest.commands.run.call_args_list[0]
    assert start.kwargs == {"user": "bridge", "background": True, "timeout": 0}
    assert start.args[0].startswith("/usr/local/bin/python3 -c ")
    assert '"127.0.0.1", 49170' in start.args[0]
    local_check = guest.commands.run.call_args_list[1].args[0]
    assert local_check.startswith("/usr/local/bin/python3 -c ")
    assert "synthetic-e2b-canary-" in local_check and "urlopen" in local_check


def test_mock_canary_cleanup_even_when_public_port_is_reachable(monkeypatch):
    guest, handle = sandbox(), SimpleNamespace(kill=Mock())
    guest.commands.run.side_effect = [handle, SimpleNamespace(exit_code=0)]
    monkeypatch.setattr(acceptance, "firewall_counters", lambda sandbox: (0, 0))
    monkeypatch.setattr(acceptance, "probe_ingress", Mock())
    monkeypatch.setattr(acceptance.httpx, "Client", lambda **kwargs: nullcontext("mock-http"))
    monkeypatch.setattr(acceptance, "probe_blocked_port", Mock(
        side_effect=acceptance.AcceptanceFailure("Port 49170 accepted a public WebSocket."),
    ))
    with pytest.raises(acceptance.AcceptanceFailure, match="Port 49170"):
        acceptance.check_isolation(guest, BASE, 5, RUN_ID)
    handle.kill.assert_called_once_with()


def test_mock_local_canary_failure_still_stops_synthetic_process(monkeypatch):
    guest, handle = sandbox(), SimpleNamespace(kill=Mock())
    guest.commands.run.side_effect = [handle, SimpleNamespace(exit_code=1)]
    monkeypatch.setattr(acceptance, "firewall_counters", lambda sandbox: (0, 0))
    monkeypatch.setattr(acceptance, "probe_ingress", Mock())
    with pytest.raises(acceptance.AcceptanceFailure, match="canary did not become ready"):
        acceptance.check_isolation(guest, BASE, 5, RUN_ID)
    handle.kill.assert_called_once_with()


@pytest.mark.parametrize("phase", ["write", "verify", "cleanup"])
def test_mock_profile_fixture_uses_real_cdp_and_synthetic_data_only(phase):
    guest = sandbox()
    guest.commands.run.return_value = SimpleNamespace(
        exit_code=0, stdout=json.dumps({"phase": phase, "ok": True}),
    )
    acceptance.profile_fixture(guest, phase, RUN_ID)
    call = guest.commands.run.call_args
    assert call.kwargs == {"user": "bridge", "timeout": 90}
    assert call.args[0].startswith("/usr/local/bin/python3 -c ")
    source = shlex.split(call.args[0])[2]
    compile(source, "<guest-fixture-not-executed>", "exec")
    assert 'connect_over_cdp("http://127.0.0.1:9222", no_defaults=True)' in source
    assert '"/data/workspace", "/data/profile"' in source
    assert "localStorage.getItem" in source and "context.cookies" in source
    assert "context.clear_cookies(name=name)" in source
    assert "browser.close" not in source
    assert OWNER not in source and KEY not in source


def test_mock_fixture_refuses_shell_injection_before_guest_command():
    guest = sandbox()
    with pytest.raises(acceptance.AcceptanceFailure, match="Invalid synthetic"):
        acceptance.profile_fixture(guest, "write", "'; print('unsafe')")
    guest.commands.run.assert_not_called()


def test_mock_profile_failure_output_is_never_echoed(state, monkeypatch, capsys):
    deps, guest, _ = mocked_dependencies(state)
    guest.commands.run.return_value = SimpleNamespace(exit_code=1, stdout=OWNER, stderr=KEY)
    monkeypatch.setattr(acceptance, "check_isolation", Mock())
    monkeypatch.setattr(acceptance, "check_process_hardening", Mock())
    monkeypatch.setattr(acceptance, "load_dependencies", lambda: deps)
    monkeypatch.setenv("E2B_API_KEY", KEY)
    assert acceptance.main(["--run-live"]) == 1
    output = capsys.readouterr().err
    assert "fixture failed" in output and OWNER not in output and KEY not in output


@pytest.fixture
def synthetic_proc(tmp_path):
    """Synthetic Linux /proc snapshots, never the executor's real process tree."""
    records = {
        1: ([b"/usr/bin/envd"], 0, False),
        10: ([b"/usr/bin/python3", b"/usr/bin/supervisord"], 1, True),
        11: ([b"/usr/local/bin/python3", b"/usr/local/bin/desktop-bridge"], 10, True),
        12: ([b"/usr/lib/chromium/chromium"], 10, True),
        13: ([b"/usr/local/bin/python3", b"-m", b"coding_tools_mcp"], 11, True),
        14: ([b"/bin/sleep", b"100"], 13, True),
        # Independent SDK commands must not be included just because their uid
        # matches bridge or they name a desktop executable in later arguments.
        20: ([b"python", b"-c", b"supervisord chromium"], 1, False),
        21: ([b"/usr/bin/tini", b"--", b"supervisord"], 1, False),
    }
    for pid, (command, parent, desktop) in records.items():
        process = tmp_path / str(pid)
        process.mkdir()
        (process / "cmdline").write_bytes(b"\0".join(command) + b"\0")
        uid = 1000 if desktop or pid == 20 else 0
        status = {"PPid": str(parent), "Uid": "\t".join([str(uid)] * 4),
                  "NoNewPrivs": "1" if desktop else "0"}
        status.update({key: "0000000000000000" if desktop else "00000000000000ff"
                       for key in ("CapEff", "CapPrm", "CapInh", "CapAmb")})
        (process / "status").write_text("\n".join(f"{key}:\t{value}" for key, value in status.items()))
    namespace = {"__name__": "synthetic_proc_test"}
    exec(compile(acceptance.GUEST_PROCESS_CHECK, "<synthetic-proc-check>", "exec"), namespace)
    return tmp_path, namespace


def test_synthetic_process_tree_checks_descendants_and_excludes_provider(synthetic_proc, capsys):
    proc, namespace = synthetic_proc
    namespace["verify_processes"](proc, uid=1000)
    assert capsys.readouterr().out == ""
    name = namespace["application_name"]
    assert name([b"/usr/bin/tini", b"--", b"supervisord"]) is None
    assert name([b"python", b"-c", b"chromium"]) is None
    assert name([b"python", b"-m", b"coding_tools_mcp"]) == b"coding_tools_mcp"


@pytest.mark.parametrize("field,value", [
    ("NoNewPrivs", "0"), ("Uid", "1000 0 1000 1000"),
    ("CapEff", "0000000000000001"), ("CapPrm", "0000000000000001"),
    ("CapInh", "0000000000000001"), ("CapAmb", "0000000000000001"),
])
def test_synthetic_unsafe_desktop_descendant_fails(synthetic_proc, field, value):
    proc, namespace = synthetic_proc
    # The unnamed shell child is also protected, not just four named categories.
    path = proc / "14/status"
    lines = path.read_text().splitlines()
    path.write_text("\n".join(f"{field}:\t{value}" if line.startswith(field + ":") else line
                              for line in lines))
    with pytest.raises(RuntimeError):
        namespace["verify_processes"](proc, uid=1000)


def test_synthetic_missing_required_desktop_process_fails(synthetic_proc):
    proc, namespace = synthetic_proc
    (proc / "13/cmdline").write_bytes(b"python\0-c\0unrelated\0")
    with pytest.raises(RuntimeError, match="category is missing"):
        namespace["verify_processes"](proc, uid=1000)


def test_synthetic_required_category_outside_desktop_tree_is_not_counted(synthetic_proc):
    proc, namespace = synthetic_proc
    path = proc / "13/status"
    path.write_text(path.read_text().replace("PPid:\t11", "PPid:\t1"))
    with pytest.raises(RuntimeError, match="category is missing"):
        namespace["verify_processes"](proc, uid=1000)


def test_synthetic_missing_privilege_field_fails(synthetic_proc):
    proc, namespace = synthetic_proc
    path = proc / "10/status"
    path.write_text("\n".join(line for line in path.read_text().splitlines()
                              if not line.startswith("CapAmb:")))
    with pytest.raises(RuntimeError, match="capabilities"):
        namespace["verify_processes"](proc, uid=1000)


def test_mock_process_check_uses_isolated_python_without_printing_sensitive_source(capsys):
    guest = sandbox()
    acceptance.check_process_hardening(guest)
    call = guest.commands.run.call_args
    assert call.kwargs == {"user": "root", "timeout": 15, "envs": {
        "HOME": "/root", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "BASH_ENV": "/dev/null", "ENV": "/dev/null",
    }}
    assert shlex.split(call.args[0])[:4] == ["/usr/bin/python3", "-I", "-S", "-c"]
    source = shlex.split(call.args[0])[4]
    assert source == acceptance.GUEST_PROCESS_CHECK
    assert "environ" not in source
    assert "print(json.dumps({\"ok\": True}))" in source
    assert capsys.readouterr().out == ""
