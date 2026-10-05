import importlib.util
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("doctor", Path(__file__).parents[1] / "scripts/doctor.py")
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    return tmp_path


def configure(monkeypatch, *, commands=True):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(doctor, "command_ok", lambda args, root: commands)


def test_missing_project_is_actionable(tmp_path):
    result = doctor.checks(tmp_path)
    assert result[0][:2] == ("FAIL", "Project")


def test_missing_docker_preserves_configuration_and_never_prints_secret(project, monkeypatch):
    secret = "private-fixture-do-not-print"
    (project / ".env").write_text("BRIDGE_OWNER_TOKEN=" + secret)
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
    result = doctor.checks(project)
    assert ("FAIL", "Docker") in [row[:2] for row in result]
    assert secret not in str(result)
    assert (project / ".env").read_text() == "BRIDGE_OWNER_TOKEN=" + secret


def test_no_env_can_still_work_with_environment_variables(project, monkeypatch):
    configure(monkeypatch)
    result = doctor.checks(project)
    assert any(row[:2] == ("WARN", "Configuration") for row in result)
    assert not any(row[0] == "FAIL" for row in result)
    assert not (project / ".env").exists()


def test_compose_unavailable(project, monkeypatch):
    configure(monkeypatch, commands=False)
    assert doctor.checks(project)[-1][:2] == ("FAIL", "Compose")


def test_daemon_failure_does_not_skip_config_validation(project, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(doctor, "command_ok", lambda args, root: "info" not in args)
    result = doctor.checks(project)
    assert ("FAIL", "Daemon") in [row[:2] for row in result]
    assert result[-1][:2] == ("OK", "Compose config")


def test_running_is_opt_in(project, monkeypatch):
    configure(monkeypatch)
    calls = []
    monkeypatch.setattr(doctor, "health_ok", lambda: calls.append(True) or True)
    doctor.checks(project)
    assert not calls
    assert doctor.checks(project, running=True)[-1][:2] == ("OK", "Local service")
    assert calls == [True]


def test_running_not_ready(project, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(doctor, "health_ok", lambda: False)
    assert doctor.checks(project, running=True)[-1][:2] == ("FAIL", "Local service")


def test_commands_are_bounded_and_do_not_reveal_stderr(project, monkeypatch):
    seen = []

    def run(args, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(returncode=1, stderr=b"secret")

    monkeypatch.setattr(doctor.subprocess, "run", run)
    assert not doctor.command_ok(["docker", "info"], project)
    assert seen == [{"cwd": project, "capture_output": True, "timeout": 15, "check": False}]


@pytest.mark.parametrize("error", [OSError("unavailable"), subprocess.TimeoutExpired("docker", 15)])
def test_command_errors_are_safe(project, monkeypatch, error):
    def run(*args, **kwargs):
        raise error
    monkeypatch.setattr(doctor.subprocess, "run", run)
    assert doctor.command_ok(["docker"], project) is False


@pytest.mark.parametrize("body,status,expected", [
    (b'{"ready": true}', 200, True),
    (b'{"ready": false}', 200, False),
    (b'{"ready": true}', 503, False),
    (b'{"ready": 1}', 200, False),
    (b'{"ok": true}', 200, False),
    (b'[]', 200, False),
    (b'not json', 200, False),
    (b'x' * 4097, 200, False),
])
def test_health_response_contract(monkeypatch, body, status, expected):
    class Response(io.BytesIO):
        pass
    response = Response(body)
    response.status = status
    handlers = []

    def build(*args):
        handlers.extend(args)
        def open_url(url, timeout):
            assert url == "http://127.0.0.1:8080/healthz"
            assert timeout == 5
            return response
        return SimpleNamespace(open=open_url)

    monkeypatch.setattr(doctor.urllib.request, "build_opener", build)
    assert doctor.health_ok() is expected
    assert handlers[0].proxies == {}
    assert handlers[1].redirect_request(None, None, None, None, None, None) is None
