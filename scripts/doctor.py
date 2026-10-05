#!/usr/bin/env python3
"""Read-only local setup checks. Never print config, tokens, or command stderr."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEALTH_URL = "http://127.0.0.1:8080/healthz"


def command_ok(args: list[str], root: Path) -> bool:
    try:
        result = subprocess.run(
            args, cwd=root, capture_output=True, timeout=15, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def health_ok() -> bool:
    # Do not follow redirects or use an environment proxy for a local check.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(HEALTH_URL, timeout=5) as response:
            data = response.read(4097)
            if response.status != 200 or len(data) > 4096:
                return False
            payload = json.loads(data)
            return isinstance(payload, dict) and payload.get("ready") is True
    except (OSError, ValueError, urllib.error.URLError):
        return False


def checks(root: Path, running: bool = False) -> list[tuple[str, str, str]]:
    results = []

    def add(status: str, name: str, message: str) -> None:
        results.append((status, name, message))

    if not (root / "compose.yaml").is_file():
        add("FAIL", "Project", "Keep this script in the repository's scripts directory.")
        return results
    if (root / ".env").is_file():
        add("OK", "Configuration", ".env exists; its contents are not displayed.")
    else:
        add("WARN", "Configuration", "No .env file. Run python3 scripts/setup.py, or supply the required environment variables yourself.")
    docker = shutil.which("docker")
    if not docker:
        add("FAIL", "Docker", "Install Docker Engine or Docker Desktop with Compose v2, then retry.")
        return results
    add("OK", "Docker", "Docker CLI found.")
    compose = [docker, "compose", "--project-directory", str(root)]
    if not command_ok([docker, "compose", "version"], root):
        add("FAIL", "Compose", "Docker Compose v2 is unavailable. Install or enable the Compose plugin.")
        return results
    add("OK", "Compose", "Docker Compose v2 is available.")
    if command_ok([docker, "info"], root):
        add("OK", "Daemon", "The Docker daemon is reachable.")
    else:
        add("FAIL", "Daemon", "Start Docker and verify your account can access it. Do not make the Docker socket world-writable.")
    if command_ok([*compose, "config", "--quiet"], root):
        add("OK", "Compose config", "Compose accepts the configuration. This does not validate remote access or secrets.")
    else:
        add("FAIL", "Compose config", "Check compose.yaml and required BRIDGE_OWNER_TOKEN configuration. Command output is hidden to avoid exposing secrets.")
    if running:
        if health_ok():
            add("OK", "Local service", "The local gateway reports ready on port 8080.")
        else:
            add("FAIL", "Local service", "Start the desktop, wait for startup, and check the local viewer. This check targets 127.0.0.1:8080 only.")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--running", action="store_true", help="also check the local gateway after startup")
    args = parser.parse_args()
    print("Agent Computer setup check (read-only)")
    results = checks(ROOT, args.running)
    for status, name, message in results:
        print(f"[{status}] {name}: {message}")
    print("These checks do not prove public HTTPS, OAuth, or ChatGPT account access. See docs/quickstart.zh-CN.md and README.md.")
    return 1 if any(status == "FAIL" for status, _, _ in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
