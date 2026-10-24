import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("preview", Path(__file__).parents[1] / "scripts/preview.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


@pytest.mark.parametrize("url", [
    "http://host.test", "https://host.test/mcp", "https://user:password@host.test",
    "https://host.test/?secret=x", "https://host.test/#fragment", "https://localhost",
    "https://127.0.0.1", "https://host.test:8080", "https://host.test\n.bad",
])
def test_preview_origin_restrictions(url):
    with pytest.raises(ValueError):
        preview.public_origin(url)


def test_preview_origin_and_configuration():
    assert preview.public_origin("https://bridge.example.com/") == "https://bridge.example.com"
    assert preview.configuration({"PREVIEW_MODE": "verify"})[:3] == ("verify", "quick", 60)
    assert preview.configuration({"BRIDGE_OWNER_TOKEN": "a" * 40})[:3] == ("preview", "quick", 60)


@pytest.mark.parametrize("env", [
    {}, {"BRIDGE_OWNER_TOKEN": "short"}, {"BRIDGE_OWNER_TOKEN": " " * 40},
    {"PREVIEW_MODE": "verify", "PREVIEW_MINUTES": "351"},
    {"PREVIEW_MODE": "verify", "PREVIEW_MINUTES": "0"},
    {"PREVIEW_MODE": "verify", "TUNNEL_KIND": "named", "PREVIEW_PUBLIC_URL": "https://x.test"},
    {"PREVIEW_MODE": "other"},
])
def test_preview_fails_closed(env):
    with pytest.raises(ValueError):
        preview.configuration(env)


def test_preview_accepts_normal_password_and_spaces():
    assert preview.configuration({"BRIDGE_OWNER_TOKEN": "eight-OK"})[0] == "preview"
    assert preview.configuration({"BRIDGE_OWNER_TOKEN": "test phrase with spaces"})[0] == "preview"


def test_preview_longest_bounded_demo():
    assert preview.configuration({"PREVIEW_MODE": "verify", "PREVIEW_MINUTES": "350"})[2] == 350


@pytest.mark.parametrize("initializer", [False, True])
def test_check_runs_without_site_packages(initializer):
    env = {"PATH": os.environ.get("PATH", ""), "PREVIEW_MODE": "verify"}
    if initializer:
        env.update(INITIALIZER_OPERATION="a" * 32, INITIALIZER_LAUNCH_ID="b" * 32,
                   INITIALIZER_URL="https://setup.example.test")
    result = subprocess.run(
        [sys.executable, "-S", str(Path(__file__).parents[1] / "scripts/preview.py"), "--check"],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Preview configuration is valid" in result.stdout


def test_dependency_free_check_still_rejects_invalid_initializer_target():
    result = subprocess.run(
        [sys.executable, "-S", str(Path(__file__).parents[1] / "scripts/preview.py"), "--check"],
        env={"PATH": os.environ.get("PATH", ""), "PREVIEW_MODE": "verify",
             "INITIALIZER_OPERATION": "a" * 32, "INITIALIZER_URL": "http://invalid.example"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "Initializer must be an HTTPS origin" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr
