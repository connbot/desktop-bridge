#!/usr/bin/env python3
"""Bounded, single-owner development preview. Never publish tokens or user data."""

import os
import re
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

SUPPORTED_MODES = {"preview", "verify"}


def public_origin(value):
    if any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("public_url must not contain whitespace or control characters")
    value = value.rstrip("/")
    url = urlsplit(value)
    if (
        url.scheme != "https" or not url.hostname or url.username or url.password
        or url.path or url.query or url.fragment or url.port not in {None, 443}
        or not re.fullmatch(r"[a-zA-Z0-9.-]+", url.hostname)
        or "." not in url.hostname or url.hostname in {"127.0.0.1", "localhost"}
    ):
        raise ValueError("public_url must be an HTTPS origin, without credentials or a path")
    return value


def configuration(env):
    mode = env.get("PREVIEW_MODE", "preview")
    kind = env.get("TUNNEL_KIND", "quick")
    minutes = int(env.get("PREVIEW_MINUTES", "60"))
    if mode not in SUPPORTED_MODES or kind not in {"quick", "named"}:
        raise ValueError("Unsupported preview mode or tunnel kind")
    if not 10 <= minutes <= 300:
        raise ValueError("Preview lifetime must be 10–300 minutes")
    owner = env.get("BRIDGE_OWNER_TOKEN", "")
    if mode == "preview" and (
        not 8 <= len(owner) <= 256 or not owner.strip()
    ):
        raise ValueError("Set repository Actions secret BRIDGE_OWNER_TOKEN (8–256 characters; not blank)")
    base = ""
    if kind == "named":
        base = public_origin(env.get("PREVIEW_PUBLIC_URL", ""))
        if not env.get("TUNNEL_TOKEN"):
            raise ValueError("Named tunnel requires Actions secret CLOUDFLARE_TUNNEL_TOKEN")
    return mode, kind, minutes, base


def summary(message):
    print(message, flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(message + "\n\n")


def main():
    mode, kind, minutes, base = configuration(os.environ)
    if "--check" in sys.argv:
        print("Preview configuration is valid. No secrets printed.")
        return
    import httpx
    from tunnel_smoke import smoke

    # Verify runs use fresh, disposable test credentials. Interactive runs require
    # the owner's preconfigured repository secret, never a public workflow input.
    owner = secrets.token_urlsafe(40) if mode == "verify" else os.environ["BRIDGE_OWNER_TOKEN"]
    print(f"::add-mask::{owner}", flush=True)
    if os.environ.get("TUNNEL_TOKEN"):
        print(f"::add-mask::{os.environ['TUNNEL_TOKEN']}", flush=True)
    cloudflared = str(Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "cloudflared")
    log = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "bridge-tunnel.log"
    args = [cloudflared, "tunnel", "--no-autoupdate", "--protocol", "http2"]
    args += ["--url", "http://127.0.0.1:8080"] if kind == "quick" else ["run"]
    tunnel_env = {k: os.environ[k] for k in ("PATH", "HOME", "TUNNEL_TOKEN") if k in os.environ}
    process = None

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        with log.open("w") as output:
            process = subprocess.Popen(args, stdout=output, stderr=output, env=tunnel_env)
        if kind == "quick":
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Cloudflare tunnel exited before publishing an address")
                match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", log.read_text())
                if match:
                    base = public_origin(match.group())
                    break
                time.sleep(1)
            if not base:
                raise RuntimeError("Cloudflare did not allocate a URL within 90 seconds")
        env = {**os.environ, "BRIDGE_OWNER_TOKEN": owner, "BRIDGE_PUBLIC_URL": base}
        subprocess.run(
            ["docker", "run", "-d", "--name", "bridge-preview", "--shm-size=1g",
             "--memory=3g", "--cpus=2", "--pids-limit=512", "-p", "127.0.0.1:8080:8080",
             "-e", "BRIDGE_OWNER_TOKEN", "-e", "BRIDGE_PUBLIC_URL", "desktop-bridge:preview"],
            env=env, check=True, stdout=subprocess.DEVNULL,
        )
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Cloudflare tunnel stopped")
                try:
                    response = client.get(base + "/healthz")
                    if response.status_code == 200 and response.json().get("ready"):
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(3)
            else:
                raise RuntimeError("Public HTTPS readiness failed; check named hostname routing to http://127.0.0.1:8080")
        # A working health check is insufficient. Verify discovery, PKCE, JSON MCP,
        # actual tools and authenticated noVNC WebSocket across the public tunnel.
        smoke(base, owner)
        if mode == "verify":
            summary("Public Cloudflare HTTPS smoke test passed. Disposable test session is now shutting down; this is not a user login URL.")
            return
        # Clear test registrations and restore READY so the first real client
        # can start without inheriting the smoke test's paused state.
        subprocess.run(["docker", "restart", "bridge-preview"], check=True, stdout=subprocess.DEVNULL)
        with httpx.Client(timeout=10) as client:
            for _ in range(60):
                try:
                    if client.get(base + "/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(2)
            else:
                raise RuntimeError("Desktop failed readiness after clearing test state")
        expiry = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(time.time() + minutes * 60))
        summary(f"## Agent Workspace development preview\n\nMCP endpoint: {base}/mcp\n\nDesktop and OAuth login: {base}\n\nAuthentication: OAuth with dynamic client registration and PKCE. Sign in using your BRIDGE_OWNER_TOKEN; never paste it into ChatGPT.\n\nScheduled stop: {expiry}. Cancel this workflow to stop early. Download needed files before stopping: the desktop and its data are disposable.\n\nQuick URLs change on restart. This session is for developing and testing Agent Workspace, not permanent hosting.")
        deadline = time.monotonic() + minutes * 60
        failures = 0
        with httpx.Client(timeout=10) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Cloudflare tunnel exited; start a new preview")
                try:
                    failures = 0 if client.get("http://127.0.0.1:8080/healthz").status_code == 200 else failures + 1
                except httpx.HTTPError:
                    failures += 1
                if failures >= 3:
                    raise RuntimeError("Desktop health check failed three times")
                time.sleep(min(15, max(0, deadline - time.monotonic())))
        summary("Preview lifetime ended. The tunnel, desktop and disposable files have been removed.")
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        subprocess.run(["docker", "rm", "-fv", "bridge-preview"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
