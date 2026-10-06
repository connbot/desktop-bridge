"""Offline launcher and bootstrap regressions; no provider or firewall is invoked."""

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launcher = load("e2b_launcher", "scripts/e2b_sandbox.py")
bootstrap = load("e2b_bootstrap", "docker/e2b-entrypoint.py")
builder = load("e2b_builder", "scripts/build_e2b_template.py")


def state():
    return {"version": 1, "sandbox_id": "sandbox123", "template": "reviewed-template",
            "public_url": "https://8080-sandbox123.e2b.app", "owner_token": "o" * 40,
            "phase": "ready"}


def sandbox():
    return SimpleNamespace(sandbox_id="sandbox123", get_host=Mock(
        return_value="8080-sandbox123.e2b.app"), commands=SimpleNamespace(run=Mock()), pause=Mock())


def test_state_permissions_no_overwrite_or_symlink(tmp_path):
    path = tmp_path / "private.json"
    launcher.write_state(path, state(), create=True)
    assert path.stat().st_mode & 0o777 == 0o600
    assert launcher.read_state(path) == state()
    with pytest.raises(FileExistsError):
        launcher.write_state(path, state(), create=True)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        launcher.read_state(link)
    with pytest.raises(OSError):
        launcher.write_state(link, state())
    path.chmod(0o644)
    with pytest.raises(ValueError):
        launcher.read_state(path)
    with pytest.raises(ValueError):
        launcher.write_state(path, state())


def test_state_update_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    launcher.write_state(path, state(), create=True)
    monkeypatch.setattr(launcher.os, "replace", Mock(side_effect=OSError("fixture")))
    with pytest.raises(OSError):
        launcher.write_state(path, {**state(), "phase": "created"})
    assert launcher.read_state(path) == state()
    assert list(tmp_path.iterdir()) == [path]


def test_create_initializes_after_provider_and_never_transmits_api_key(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    remote = sandbox()
    sdk = Mock()
    sdk.create.return_value = remote
    monkeypatch.setenv("E2B_API_KEY", "synthetic-provider-key")
    monkeypatch.setattr(launcher, "wait_ready", Mock())
    result = launcher.create(sdk, path, "reviewed-template", 3600)
    options = sdk.create.call_args.kwargs
    assert options["lifecycle"] == {"on_timeout": {"action": "pause", "keep_memory": True},
                                    "auto_resume": False}
    assert options["network"] == {"allow_public_traffic": True}
    assert "envs" not in options
    start = remote.commands.run.call_args_list[0]
    assert start.args == (launcher.START_COMMAND,)
    assert set(start.kwargs["envs"]) == {
        "BRIDGE_OWNER_TOKEN", "BRIDGE_PUBLIC_URL", *launcher.ROOT_COMMAND_ENV,
    }
    for call in remote.commands.run.call_args_list:
        assert call.kwargs["user"] == "root"
        for key, value in launcher.ROOT_COMMAND_ENV.items():
            assert call.kwargs["envs"][key] == value
    assert start.kwargs["user"] == "root" and start.kwargs["background"] is True
    assert start.kwargs["timeout"] == 0
    assert "synthetic-provider-key" not in json.dumps(result)
    assert "synthetic-provider-key" not in str(remote.commands.run.call_args_list)
    assert result["phase"] == "ready" and launcher.read_state(path) == result
    remote.pause.assert_not_called()
    with pytest.raises(FileExistsError):
        launcher.create(sdk, path, "reviewed-template", 3600)
    assert sdk.create.call_count == 1


def test_failed_start_retains_id_and_pauses_never_kills(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    remote = sandbox()
    sdk = Mock()
    sdk.create.return_value = remote
    monkeypatch.setattr(launcher, "wait_ready", Mock(side_effect=RuntimeError("test")))
    with pytest.raises(RuntimeError):
        launcher.create(sdk, path, "reviewed-template", 3600)
    assert launcher.read_state(path)["phase"] == "created"
    remote.pause.assert_called_once_with(keep_memory=True)
    sdk.kill.assert_not_called()
    with pytest.raises(ValueError):
        launcher.resume(sdk, path, 3600)
    sdk.connect.assert_not_called()


def test_resume_keeps_secrets_and_runtime_and_checks_origin(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    launcher.write_state(path, state(), create=True)
    remote = sandbox()
    sdk = Mock()
    sdk.connect.return_value = remote
    monkeypatch.setattr(launcher, "wait_ready", Mock())
    assert launcher.resume(sdk, path, 3600) == state()
    sdk.create.assert_not_called()
    sdk.connect.assert_called_once_with("sandbox123", timeout=3600)
    assert remote.commands.run.call_args.args == (launcher.START_COMMAND + " --check",)
    assert remote.commands.run.call_args.kwargs["envs"] == launcher.ROOT_COMMAND_ENV
    remote.get_host.return_value = "different.e2b.app"
    with pytest.raises(ValueError):
        launcher.resume(sdk, path, 3600)
    remote.pause.assert_called_once_with(keep_memory=True)


def test_status_does_not_connect_and_errors_hide_secrets(tmp_path, monkeypatch, capsys):
    e2b = pytest.importorskip("e2b")
    path = tmp_path / "state.json"
    launcher.write_state(path, state(), create=True)
    monkeypatch.setenv("E2B_API_KEY", "synthetic-key")
    sdk = Mock()
    sdk.get_info.return_value = SimpleNamespace(state="paused")
    monkeypatch.setattr(e2b, "Sandbox", sdk)
    assert launcher.main(["--state", str(path), "status"]) == 0
    sdk.get_info.assert_called_once_with("sandbox123")
    sdk.connect.assert_not_called()
    assert "paused" in capsys.readouterr().out
    sdk.get_info.side_effect = RuntimeError("synthetic-secret-private-contents")
    assert launcher.main(["--state", str(path), "status"]) == 1
    assert "synthetic-secret" not in str(capsys.readouterr())


def test_delete_requires_matching_id(tmp_path, monkeypatch):
    e2b = pytest.importorskip("e2b")
    path = tmp_path / "state.json"
    launcher.write_state(path, state(), create=True)
    monkeypatch.setenv("E2B_API_KEY", "synthetic-key")
    sdk = Mock()
    monkeypatch.setattr(e2b, "Sandbox", sdk)
    assert launcher.main(["--state", str(path), "delete", "--confirm-id", "wrong"]) == 1
    sdk.kill.assert_not_called()
    assert path.exists()
    assert launcher.main(["--state", str(path), "delete", "--confirm-id", "sandbox123"]) == 0
    sdk.kill.assert_called_once_with("sandbox123")
    assert not path.exists()


def test_both_firewalls_fail_closed_and_preserve_provider_rules():
    runner = Mock()
    bootstrap.secure_ingress(runner)
    commands = [call.args[0] for call in runner.call_args_list]
    assert len(commands) == 14
    assert commands[0][0].endswith("iptables-legacy")
    assert commands[7][0].endswith("ip6tables-legacy")
    assert all("-F" not in command and "OUTPUT" not in command for command in commands)
    assert commands[6][2:] == ["-I", "INPUT", "1", "-j", bootstrap.CHAIN]
    assert commands[13][2:] == commands[6][2:]
    assert all("49999" not in str(command) for command in commands)
    runner = Mock(side_effect=OSError("No permission"))
    with pytest.raises(OSError):
        bootstrap.secure_ingress(runner)
    assert runner.call_count == 1


def test_runtime_environment_allowlist():
    environment = bootstrap.runtime_environment({
        "BRIDGE_OWNER_TOKEN": "t" * 40, "BRIDGE_PUBLIC_URL": "https://8080-demo.e2b.app",
        "E2B_API_KEY": "never-copy", "ENVD_ACCESS_TOKEN": "never-copy", "PYTHONPATH": "/tmp",
    })
    assert "never-copy" not in str(environment)
    assert "PYTHONPATH" not in environment
    assert environment["DISPLAY"] == ":99" and environment["HOME"] == "/home/bridge"
    with pytest.raises(ValueError):
        bootstrap.runtime_environment({})


def test_bootstrap_stops_before_exec_on_either_firewall_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap.sys, "argv", ["entrypoint"])
    monkeypatch.setattr(bootstrap.os, "geteuid", lambda: 0)
    monkeypatch.setattr(bootstrap.os, "umask", Mock())
    monkeypatch.setattr(bootstrap, "verify_template", Mock())
    monkeypatch.setattr(bootstrap, "runtime_environment", lambda _: {})
    monkeypatch.setattr(bootstrap.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=1000,pw_gid=1000))
    monkeypatch.setattr(bootstrap, "LATCH", tmp_path / "latch")
    monkeypatch.setattr(bootstrap.runpy, "run_path", lambda _: {"prepare_data": Mock()})
    monkeypatch.setattr(bootstrap, "secure_ingress", Mock(side_effect=RuntimeError("firewall")))
    executor = Mock()
    monkeypatch.setattr(bootstrap.os, "execve", executor)
    with pytest.raises(RuntimeError):
        bootstrap.main()
    executor.assert_not_called()
    assert not bootstrap.LATCH.exists()


def test_bootstrap_drops_capabilities_and_privilege(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap.sys, "argv", ["entrypoint"])
    monkeypatch.setattr(bootstrap.os, "geteuid", lambda: 0)
    monkeypatch.setattr(bootstrap.os, "umask", Mock())
    monkeypatch.setattr(bootstrap, "verify_template", Mock())
    monkeypatch.setattr(bootstrap, "runtime_environment", lambda _: {"HOME": "/home/bridge"})
    monkeypatch.setattr(bootstrap.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=1000,pw_gid=1000))
    monkeypatch.setattr(bootstrap, "LATCH", tmp_path / "latch")
    monkeypatch.setattr(bootstrap.runpy, "run_path", lambda _: {"prepare_data": Mock()})
    monkeypatch.setattr(bootstrap, "secure_ingress", Mock())
    monkeypatch.setattr(bootstrap, "verify_ingress", Mock())
    executor = Mock()
    monkeypatch.setattr(bootstrap.os, "execve", executor)
    bootstrap.main()
    executable, command, environment = executor.call_args.args
    assert executable == "/usr/bin/setpriv"
    assert {"--reuid=1000", "--regid=1000", "--clear-groups", "--no-new-privs",
            "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all"} <= set(command)
    assert environment == {"HOME": "/home/bridge"}


def test_template_real_sdk_serialization_is_cold_and_pinned():
    e2b = pytest.importorskip("e2b")
    with pytest.raises(ValueError):
        builder.make_template("desktop-bridge:latest")
    image = "ghcr.io/example/desktop@sha256:" + "a" * 64
    template = builder.make_template(image)
    # Actual pinned SDK serialization, never Template.build or a provider call.
    payload = json.loads(e2b.Template.to_json(template))
    assert payload["fromImage"] == image
    assert payload["startCmd"] == "sleep infinity"
    assert payload["readyCmd"] == launcher.START_COMMAND + " --prepare-template"
    assert payload["steps"][:2] == [
        {"type": "USER", "args": ["root"], "force": False},
        {"type": "WORKDIR", "args": ["/app"], "force": False},
    ]
    environment_steps = payload["steps"][2:]
    assert environment_steps
    assert len(environment_steps) == 1 and environment_steps[0]["type"] == "ENV"
    args = environment_steps[0]["args"]
    assert dict(zip(args[::2], args[1::2], strict=True)) == launcher.ROOT_COMMAND_ENV
    assert "BRIDGE_OWNER_TOKEN" not in str(payload)
    assert "E2B_API_KEY" not in str(payload)
    assert os.access(ROOT / "Dockerfile", os.R_OK)


def test_native_windows_stops_before_provider_or_state(monkeypatch, capsys):
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    with pytest.raises(SystemExit) as error:
        launcher.main(["status"])
    assert error.value.code == 2
    assert "WSL" in capsys.readouterr().err
