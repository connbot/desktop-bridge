#!/usr/bin/env python3
"""Opt-in checks against an ALREADY CREATED, disposable E2B desktop.

This is not a unit test or a deployment command. Run only after approving real
provider use, the guest firewall, and temporary synthetic browser/workspace data.
It authenticates with the saved owner token, pauses/restores the same sandbox,
and leaves it running. It never creates or deletes a sandbox. Keep the state
file and E2B_API_KEY private. A failed check is not proof of provider isolation.

Example, after securely supplying E2B_API_KEY and creating a ready sandbox:
    python scripts/e2b_acceptance.py --run-live --state /private/e2b.json

Only successful runs remove their synthetic profile fixtures. Browser state is
checked only for a synthetic local-origin key/cookie and never printed.
Exceptions from the provider, HTTP client and guest
are deliberately suppressed because they can contain tokens or cookies.
"""

import argparse
import json
import os
import re
import shlex
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect as websocket_connect

DEFAULT_STATE = Path.home() / ".local/state/agent-computer/e2b.json"
BLOCKED_PORTS = (5900, 5901, 9222, 49999)
GATEWAY_FAILURES = {502, 503, 504}
CANARY_PORT = 49170
FIREWALL_CHECK = "/usr/bin/python3 -I -S /opt/agent-computer/e2b-entrypoint.py --check"
# SDK commands start a login shell before our Python/iptables executable. Set
# these at the SDK boundary so root never sources the bridge user's home files.
ROOT_COMMAND_ENV = {
    "HOME": "/root", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "BASH_ENV": "/dev/null", "ENV": "/dev/null",
}


class AcceptanceFailure(RuntimeError):
    """A deliberately sanitized diagnostic, safe to show on the console."""


def require(condition, message):
    if not condition:
        raise AcceptanceFailure(message)


def load_dependencies():
    # Delayed until the CLI consent/key checks; importing never contacts E2B.
    from e2b import Sandbox
    from e2b_sandbox import public_origin, read_state, wait_ready
    from tunnel_smoke import smoke

    return SimpleNamespace(
        Sandbox=Sandbox, public_origin=public_origin, read_state=read_state,
        wait_ready=wait_ready, smoke=smoke,
    )


def validate_state(state):
    require(isinstance(state, dict), "Invalid state file.")
    require(state.get("version") == 1 and state.get("phase") == "ready",
            "Acceptance requires a version 1, ready launcher state file.")
    for field in ("sandbox_id", "public_url", "owner_token", "template"):
        require(isinstance(state.get(field), str) and bool(state[field].strip()),
                "The launcher state file is incomplete.")
    base = state["public_url"]
    parsed = urlsplit(base)
    require(parsed.scheme == "https" and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None
            and parsed.port is None and not parsed.query and not parsed.fragment
            and not parsed.path and not any(char.isspace() for char in base),
            "The saved public URL must be a bare HTTPS origin.")


def native_origin(sandbox, port):
    host = sandbox.get_host(port)
    require(isinstance(host, str) and re.fullmatch(r"[A-Za-z0-9.-]+", host) is not None,
            "E2B returned an invalid native host.")
    return "https://" + host


def check_blocked_response(status, body, port, protocol):
    # A 401/403/404 is still a response from a reachable service, not evidence
    # that guest ingress was blocked. Reject raw VNC/CDP signatures even if an
    # intermediary attached a generic gateway error status.
    raw = body.lower() if isinstance(body, bytes) else str(body).lower().encode()
    signatures = (b"rfb ", b"websocketdebuggerurl", b"devtools/browser", b"vnc server",
                  b"synthetic-e2b-canary-")
    require(status in GATEWAY_FAILURES and not any(value in raw for value in signatures),
            f"Port {port} {protocol} reached a service or returned an inconclusive response.")


def probe_blocked_port(http, sandbox, base, port, timeout):
    origin = native_origin(sandbox, port)
    try:
        response = http.get(origin + "/")
    except httpx.TimeoutException:
        pass
    else:
        check_blocked_response(response.status_code, response.content, port, "HTTP")
    try:
        with websocket_connect(
            origin.replace("https://", "wss://", 1) + "/", origin=base,
            open_timeout=timeout, close_timeout=1, proxy=None,
        ):
            raise AcceptanceFailure(f"Port {port} accepted a public WebSocket.")
    except InvalidStatus as error:
        check_blocked_response(
            error.response.status_code, error.response.body, port, "WebSocket",
        )
    except TimeoutError:
        pass


def probe_ingress(sandbox, base, timeout=5):
    """Unauthenticated native HTTPS and WebSocket checks, with no app cookies.

    Gateway failures and timeouts are accepted only alongside the positive 8080
    control and live guest firewall checks. DNS/TLS errors, connection resets,
    malformed handshakes, redirects and all other HTTP responses fail closed.
    """
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as http:
        require(http.get(base + "/healthz").status_code == 200,
                "The positive public 8080 health check failed.")
        require(http.get(base + "/api/status").status_code == 401,
                "Unauthenticated public app status was not rejected.")
        for port in BLOCKED_PORTS:
            probe_blocked_port(http, sandbox, base, port, timeout)

        # envd is deliberately reachable for the SDK, but no token is supplied
        # here. List is a read-only RPC; a gateway failure does NOT prove auth.
        response = http.post(
            native_origin(sandbox, 49983) + "/process.Process/List", json={},
            headers={"Connect-Protocol-Version": "1"},
        )
        require(response.status_code in {401, 403},
                "Unauthenticated envd was not explicitly rejected.")
        try:
            with websocket_connect(
                base.replace("https://", "wss://", 1) + "/desktop/view", origin=base,
                subprotocols=["binary"], open_timeout=timeout, close_timeout=1, proxy=None,
            ):
                raise AcceptanceFailure("The public viewer accepted an unauthenticated WebSocket.")
        except InvalidStatus as error:
            require(error.response.status_code in {401, 403},
                    "The public viewer did not explicitly reject an unauthenticated WebSocket.")
        require(http.get(base + "/healthz").status_code == 200,
                "Public app connectivity failed after the negative ingress probes.")


GUEST_PROCESS_CHECK = r'''
import json
import pwd
from pathlib import Path

def application_name(command):
    for part in command[:2]:
        name = part.rsplit(b"/", 1)[-1]
        if name in {b"supervisord", b"desktop-bridge", b"chromium"}:
            return name
    if command[1:3] == [b"-m", b"coding_tools_mcp"]:
        return b"coding_tools_mcp"
    return None

def verify_processes(proc=Path("/proc"), uid=None):
    uid = pwd.getpwnam("bridge").pw_uid if uid is None else uid
    if uid == 0:
        raise RuntimeError("Desktop account must not be root")
    records = {}
    for process in proc.iterdir():
        if not process.name.isdigit():
            continue
        try:
            command = (process / "cmdline").read_bytes().split(b"\0")[:3]
            status = dict(line.split(":", 1) for line in (process / "status").read_text().splitlines())
            records[int(process.name)] = (int(status["PPid"]), application_name(command), status)
        except FileNotFoundError:
            continue
    roots = {pid for pid, (_, name, _) in records.items() if name == b"supervisord"}
    if len(roots) != 1:
        raise RuntimeError("Expected one desktop supervisor")
    tree = set(roots)
    while True:
        children = {pid for pid, (parent, _, _) in records.items() if parent in tree} - tree
        if not children:
            break
        tree.update(children)
    seen = set()
    for pid in tree:
        _, name, status = records[pid]
        if [int(value) for value in status.get("Uid", "").split()] != [uid] * 4:
            raise RuntimeError("Desktop subtree does not run exclusively as bridge")
        if status.get("NoNewPrivs", "").strip() != "1":
            raise RuntimeError("Desktop subtree can acquire new privileges")
        for capability in ("CapEff", "CapPrm", "CapInh", "CapAmb"):
            if int(status.get(capability, "-1"), 16) != 0:
                raise RuntimeError("Desktop subtree retains capabilities")
        seen.add(name)
    if not {b"supervisord", b"desktop-bridge", b"chromium", b"coding_tools_mcp"} <= seen:
        raise RuntimeError("Required desktop process category is missing")

if __name__ == "__main__":
    verify_processes()
    print(json.dumps({"ok": True}))
'''


def check_process_hardening(sandbox):
    # Inspect only: never print environment variables or command lines. The
    # supervisor's actual descendants are checked, excluding independent envd
    # and SDK commands whose credentials/capabilities have a different purpose.
    result = sandbox.commands.run(
        "/usr/bin/python3 -I -S -c " + shlex.quote(GUEST_PROCESS_CHECK), user="root", timeout=15,
        envs=ROOT_COMMAND_ENV,
    )
    require(result.exit_code == 0, "Live desktop process privilege verification failed.")
    require(json.loads(result.stdout) == {"ok": True},
            "Live desktop process verification returned an unexpected result.")


def firewall_counters(sandbox):
    # The trusted launcher independently validates both exact allowlists and
    # first INPUT jumps. Read counters without flushing/changing provider rules.
    result = sandbox.commands.run(
        FIREWALL_CHECK, user="root", timeout=15, envs=ROOT_COMMAND_ENV,
    )
    require(result.exit_code == 0, "The live guest firewall validation failed.")
    counters = []
    for binary in ("iptables-legacy-save", "ip6tables-legacy-save"):
        result = sandbox.commands.run(
            f"/usr/sbin/{binary} -c -t filter", user="root", timeout=15,
            envs=ROOT_COMMAND_ENV,
        )
        require(result.exit_code == 0, "Could not read the live guest firewall counters.")
        matches = re.findall(
            r"^\[(\d+):\d+\] -A BRIDGE_E2B_INPUT -p tcp -j DROP$", result.stdout, re.M,
        )
        require(len(matches) == 1, "The live guest TCP DROP counter is missing or ambiguous.")
        counters.append(int(matches[0]))
    return tuple(counters)


def check_isolation(sandbox, base, timeout, run_id):
    firewall_counters(sandbox)
    probe_ingress(sandbox, base, timeout)
    # HTTP-to-VNC failure alone is weak evidence because VNC is not HTTP. This
    # disposable loopback HTTP service proves the forwarding firewall boundary
    # without stopping a real desktop service. An occupied port fails closed.
    marker = "synthetic-e2b-canary-" + run_id
    source = f'''
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write({marker.encode()!r})
    def log_message(self, *args):
        pass
HTTPServer(("127.0.0.1", {CANARY_PORT}), Handler).serve_forever()
'''
    handle = sandbox.commands.run(
        "/usr/local/bin/python3 -c " + shlex.quote(source),
        user="bridge", background=True, timeout=0,
    )
    try:
        local_check = f'''
import time
from urllib.request import urlopen
for attempt in range(20):
    try:
        with urlopen("http://127.0.0.1:{CANARY_PORT}/", timeout=1) as response:
            if response.status != 200 or response.read() != {marker.encode()!r}:
                raise RuntimeError("Wrong local canary")
        break
    except OSError:
        if attempt == 19:
            raise
        time.sleep(0.1)
'''
        result = sandbox.commands.run(
            "/usr/local/bin/python3 -c " + shlex.quote(local_check), user="bridge", timeout=30,
        )
        require(result.exit_code == 0, "The loopback HTTP canary did not become ready.")
        before = firewall_counters(sandbox)
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as http:
            probe_blocked_port(http, sandbox, base, CANARY_PORT, timeout)
        after = firewall_counters(sandbox)
        require(all(end >= start for start, end in zip(before, after, strict=True))
                and sum(after) > sum(before),
                "Public canary probes did not increment guest TCP DROP counters.")
    finally:
        # Only terminate this synthetic process, never the E2B sandbox.
        handle.kill()


GUEST_FIXTURE = r'''
import asyncio
import json
import os
import time
from pathlib import Path
from playwright.async_api import async_playwright

async def run(phase, run_id):
    name = "e2b_acceptance_" + run_id
    value = "synthetic-" + run_id
    paths = [Path(root) / ("." + name) for root in ("/data/workspace", "/data/profile")]
    if phase == "write":
        for path in paths:
            with path.open("x") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
    elif phase == "verify":
        if any(path.read_text() != value for path in paths):
            raise RuntimeError("Synthetic filesystem marker did not persist")
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp("http://127.0.0.1:9222", no_defaults=True)
        context = browser.contexts[0]
        page = await context.new_page()
        try:
            await page.goto("http://127.0.0.1:8080/static/demo.html", wait_until="domcontentloaded")
            if phase == "write":
                await page.evaluate("([key, value]) => localStorage.setItem(key, value)", [name, value])
                await context.add_cookies([{
                    "name": name, "value": value, "url": "http://127.0.0.1:8080",
                    "expires": time.time() + 3600, "sameSite": "Strict",
                }])
            elif phase == "verify":
                stored = await page.evaluate("key => localStorage.getItem(key)", name)
                cookies = await context.cookies("http://127.0.0.1:8080")
                if stored != value or not any(c["name"] == name and c["value"] == value for c in cookies):
                    raise RuntimeError("Synthetic Chromium profile state did not persist")
            elif phase == "cleanup":
                await page.evaluate("key => localStorage.removeItem(key)", name)
                await context.clear_cookies(name=name)
                for path in paths:
                    path.unlink(missing_ok=True)
        finally:
            await page.close()
        # Do not close Chromium, which belongs to the running desktop.
    os.sync()
    print(json.dumps({"phase": phase, "ok": True}))
'''


def profile_fixture(sandbox, phase, run_id):
    require(phase in {"write", "verify", "cleanup"}
            and re.fullmatch(r"[a-f0-9]{32}", run_id) is not None,
            "Invalid synthetic fixture request.")
    source = GUEST_FIXTURE + f"\nasyncio.run(run({phase!r}, {run_id!r}))\n"
    result = sandbox.commands.run(
        "/usr/local/bin/python3 -c " + shlex.quote(source), user="bridge", timeout=90,
    )
    require(result.exit_code == 0, "Guest browser/profile fixture failed.")
    # No stdout/stderr is echoed: even a provider exception can contain secrets.
    require(json.loads(result.stdout) == {"phase": phase, "ok": True},
            "Guest browser/profile fixture returned an unexpected result.")


def run_live(state, deps, sandbox_timeout=600, probe_timeout=5):
    stage = "connecting to the existing sandbox"
    try:
        sandbox = deps.Sandbox.connect(state["sandbox_id"], timeout=sandbox_timeout)
        require(sandbox.sandbox_id == state["sandbox_id"], "E2B connected to a different sandbox.")
        base = deps.public_origin(sandbox)
        require(base == state["public_url"],
                "The native public origin does not match the saved state; owner token withheld.")
        stage = "waiting for the public app"
        deps.wait_ready(base)
        stage = "checking live desktop process privileges"
        check_process_hardening(sandbox)
        run_id = uuid.uuid4().hex
        stage = "checking initial ingress and authentication"
        check_isolation(sandbox, base, probe_timeout, run_id)
        stage = "running authenticated OAuth, MCP and viewer acceptance before pause"
        deps.smoke(base, state["owner_token"], action_id=f"e2b-{run_id}-before")
        stage = "writing synthetic workspace and real Chromium profile fixtures"
        profile_fixture(sandbox, "write", run_id)
        stage = "pausing the existing sandbox"
        require(deps.Sandbox.pause(state["sandbox_id"], keep_memory=True) is True,
                "E2B did not confirm a new memory-preserving pause.")
        stage = "resuming the same sandbox with fresh SDK connections"
        # Never reuse transports across hibernation. The new SDK, HTTP, OAuth and
        # viewer connections below exercise recovery after old connections end.
        resumed = deps.Sandbox.connect(state["sandbox_id"], timeout=sandbox_timeout)
        require(resumed.sandbox_id == sandbox.sandbox_id,
                "Sandbox identity changed after resume.")
        require(deps.public_origin(resumed) == base,
                "Native public origin changed after resume; owner token withheld.")
        stage = "waiting for the restored public app"
        deps.wait_ready(base)
        stage = "checking restored desktop process privileges"
        check_process_hardening(resumed)
        stage = "checking restored ingress and authentication"
        check_isolation(resumed, base, probe_timeout, run_id)
        stage = "checking persisted workspace and real Chromium profile fixtures"
        profile_fixture(resumed, "verify", run_id)
        stage = "reconnecting OAuth, MCP and the authenticated viewer after resume"
        deps.smoke(base, state["owner_token"], action_id=f"e2b-{run_id}-after")
        stage = "removing only this run's synthetic fixtures"
        profile_fixture(resumed, "cleanup", run_id)
    except AcceptanceFailure:
        raise
    except Exception:
        raise AcceptanceFailure(f"Acceptance failed while {stage}; sensitive details suppressed.") from None
    print(
        "PASS live E2B: non-root no-new-privileges desktop subtree, native-origin auth and ingress, "
        "OAuth/PKCE, real MCP desktop/browser/shell, "
        "authenticated viewer, memory-preserving pause/resume, stable ID/origin, "
        "workspace and Chromium localStorage/cookie persistence, fresh connections. "
        "Sandbox remains running; no provider resources were created or deleted.",
        flush=True,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true", help="approve live checks and pause/resume")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--timeout", "--sandbox-timeout", dest="sandbox_timeout", type=int,
                        default=600, help="active lifetime on connect/resume, seconds (default 600)")
    parser.add_argument("--probe-timeout", type=int, default=5,
                        help="each unauthenticated ingress probe timeout, seconds (default 5)")
    args = parser.parse_args(argv)
    if not args.run_live:
        parser.error("Live acceptance is opt-in. Obtain provider/firewall approval, then use --run-live.")
    if not os.environ.get("E2B_API_KEY", "").strip():
        parser.error("E2B_API_KEY is required; supply it securely, never as a command argument.")
    if not 300 <= args.sandbox_timeout <= 3600 or not 1 <= args.probe_timeout <= 30:
        parser.error("Use a sandbox timeout of 300–3600 seconds and probe timeout of 1–30 seconds.")
    try:
        deps = load_dependencies()
        state = deps.read_state(args.state)
        validate_state(state)
        run_live(state, deps, args.sandbox_timeout, args.probe_timeout)
    except AcceptanceFailure as error:
        print(f"FAIL: {error}", file=sys.stderr)
        print("No sandbox was deleted. Check launcher status; the sandbox may be paused.", file=sys.stderr)
        return 1
    except Exception:
        print("FAIL: Could not load the E2B SDK or a valid private launcher state; details suppressed.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
