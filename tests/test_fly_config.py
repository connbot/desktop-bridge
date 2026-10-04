import importlib.util
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configure_fly", ROOT / "scripts/configure_fly.py")
configure_fly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure_fly)


def test_rendered_config_has_explicit_safe_topology():
    text = configure_fly.render_config("bridge-demo-123", "sjc")
    config = tomllib.loads(text)
    assert "__APP_NAME__" not in text and "__REGION__" not in text
    assert config["app"] == "bridge-demo-123"
    assert config["primary_region"] == "sjc"
    assert config["build"]["dockerfile"] == "Dockerfile"
    assert config["build"]["build-target"] == "fly"
    assert config["build"]["args"]["BRIDGE_PYTHON_EXTRAS"] == "desktop,postgres"
    assert config["env"]["BRIDGE_PUBLIC_URL"] == "https://bridge-demo-123.fly.dev"
    assert config["env"]["BRIDGE_CODING_PERMISSION_MODE"] == "safe"
    assert config["mounts"] == [
        {"source": "desktop_data", "destination": "/data", "initial_size": "10gb"}
    ]
    http = config["http_service"]
    assert http["internal_port"] == 8080 and http["force_https"] is True
    assert http["auto_stop_machines"] == "off"
    assert http["checks"][0]["headers"]["Host"] == "bridge-demo-123.fly.dev"
    assert http["checks"][0]["path"] == "/healthz"
    assert "services" not in config  # No raw VNC/CDP ingress.
    assert "release_command" not in config.get("deploy", {})  # Mount absent during release.
    assert config["vm"] == [{"cpu_kind": "shared", "cpus": 2, "memory": "4gb"}]
    assert not any("TOKEN" in key or "KEY" in key or "DATABASE" in key for key in config["env"])


@pytest.mark.parametrize("app", [
    "", "-bridge", "bridge-", "UPPER", "has space", "a" * 64,
    "bridge\n[env]", 'bad"app', "postgresql://user:secret@host/db",
])
def test_bad_app_names_rejected(app):
    with pytest.raises(ValueError):
        configure_fly.render_config(app, "sjc")


@pytest.mark.parametrize("region", ["", "us-east-1", "SJC", "sjc\n", 'a"b'])
def test_bad_region_codes_rejected(region):
    with pytest.raises(ValueError):
        configure_fly.render_config("bridge-demo", region)


def test_cli_writes_once_and_never_overwrites(tmp_path, capsys):
    output = tmp_path / "fly.toml"
    args = ["--app", "bridge-demo", "--region", "sjc", "--output", str(output)]
    assert configure_fly.main(args) == 0
    original = output.read_text()
    assert tomllib.loads(original)["app"] == "bridge-demo"
    with pytest.raises(SystemExit) as error:
        configure_fly.main(args)
    assert error.value.code == 2
    assert output.read_text() == original
    assert "No account, resources or deployment" in capsys.readouterr().out


def test_cli_does_not_follow_existing_output_symlink(tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("keep")
    link = tmp_path / "fly.toml"
    link.symlink_to(target)
    with pytest.raises(SystemExit):
        configure_fly.main(["--app", "bridge", "--region", "sjc", "--output", str(link)])
    assert target.read_text() == "keep"


def test_fly_target_is_explicit_and_default_remains_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "FROM python:3.12-slim-bookworm AS desktop" in dockerfile
    assert "USER bridge" in dockerfile.split("FROM desktop AS fly")[0]
    fly_stage = dockerfile.split("FROM desktop AS fly")[1].split("FROM desktop AS local")[0]
    assert "USER root" in fly_stage
    assert "fly-entrypoint.py" in fly_stage
    assert dockerfile.rstrip().endswith("FROM desktop AS local")


def test_docker_context_is_allowlisted():
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert "**" in ignore
    for item in ("!Dockerfile", "!src/**", "!docker/**", "**/.env*", "**/.git"):
        assert item in ignore
    assert "!workspace/**" not in ignore and "!.env" not in ignore
