import importlib.util
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
    {}, {"BRIDGE_OWNER_TOKEN": "short"}, {"BRIDGE_OWNER_TOKEN": "a" * 40 + "\n"},
    {"PREVIEW_MODE": "verify", "PREVIEW_MINUTES": "301"},
    {"PREVIEW_MODE": "verify", "PREVIEW_MINUTES": "0"},
    {"PREVIEW_MODE": "verify", "TUNNEL_KIND": "named", "PREVIEW_PUBLIC_URL": "https://x.test"},
    {"PREVIEW_MODE": "other"},
])
def test_preview_fails_closed(env):
    with pytest.raises(ValueError):
        preview.configuration(env)
