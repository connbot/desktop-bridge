#!/usr/bin/env python3
"""Create or manage one E2B Agent Computer; provider keys stay on this computer."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_STATE = Path.home() / ".local/state/agent-computer/e2b.json"
START_COMMAND = "/usr/bin/python3 -I -S /opt/agent-computer/e2b-entrypoint.py"
# The SDK starts a login shell BEFORE this command. Override HOME there, not
# inside the shell: image HOME=/home/bridge is writable by the desktop owner.
ROOT_COMMAND_ENV = {"HOME": "/root", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "BASH_ENV": "/dev/null", "ENV": "/dev/null"}


def public_origin(sandbox) -> str:
    host = sandbox.get_host(8080)
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or "." not in host:
        raise ValueError("E2B returned an invalid gateway hostname")
    return "https://" + host


def read_state(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError("State must be an owner-only regular file")
        with os.fdopen(fd, "r") as stream:
            fd = -1
            state = json.load(stream)
    finally:
        if fd >= 0:
            os.close(fd)
    if (
        state.get("version") != 1
        or not re.fullmatch(r"[a-zA-Z0-9_-]+", state.get("sandbox_id", ""))
        or not isinstance(state.get("owner_token"), str)
        or len(state["owner_token"]) < 32
        or state.get("phase") not in {"created", "ready"}
    ):
        raise ValueError("Invalid state; do not create a replacement over it")
    origin = urlsplit(state.get("public_url", ""))
    if origin.scheme != "https" or not origin.hostname or origin.path or origin.query or origin.fragment:
        raise ValueError("Invalid public origin in state")
    return state


def write_state(path: Path, state: dict, *, create: bool = False) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not create:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                    or info.st_uid != os.getuid()):
                raise ValueError("State must be an owner-only regular file")
        finally:
            os.close(fd)
    target = path if create else path.with_name("." + path.name + "." + secrets.token_hex(8))
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if not create:
            os.replace(target, path)
    finally:
        if not create:
            target.unlink(missing_ok=True)


def wait_ready(base_url: str, timeout: float = 180) -> None:
    import httpx

    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(base_url + "/healthz")
                if response.status_code == 200 and response.json().get("ready") is True:
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(2)
    raise RuntimeError("Gateway readiness timed out")


def show_ready(state: dict, path: Path) -> None:
    print("Desktop and OAuth login: " + state["public_url"])
    print("MCP endpoint: " + state["public_url"] + "/mcp")
    print("Sandbox: " + state["sandbox_id"])
    print("Owner login token is in your private state file: " + str(path))
    print("Open that file locally. Never paste its token or your E2B key into chat or logs.")
    print("The runtime pauses at its deadline. Use resume to wake it; pause to stop compute.")


def create(sdk, path: Path, template: str, timeout: int) -> dict:
    # Reserve the local file BEFORE calling the provider. Parallel create calls
    # cannot allocate two sandboxes for one state file.
    write_state(path, {"version": 1, "phase": "allocating"}, create=True)
    sandbox = None
    try:
        sandbox = sdk.create(
            template=template,
            timeout=timeout,
            metadata={"app": "agent-computer", "launcher": "1"},
            lifecycle={"on_timeout": {"action": "pause", "keep_memory": True},
                       "auto_resume": False},
            network={"allow_public_traffic": True},
        )
        state = {
            "version": 1,
            "sandbox_id": sandbox.sandbox_id,
            "template": template,
            "public_url": public_origin(sandbox),
            "owner_token": secrets.token_urlsafe(40),
            "phase": "created",
        }
        # Recovery information survives startup failures and partial readiness.
        write_state(path, state)
        sandbox.commands.run(
            START_COMMAND,
            user="root",
            cwd="/",
            envs={
                **ROOT_COMMAND_ENV,
                "BRIDGE_OWNER_TOKEN": state["owner_token"],
                "BRIDGE_PUBLIC_URL": state["public_url"],
            },
            background=True,
            timeout=0,
        )
        wait_ready(state["public_url"])
        # Check the startup latch through the secured control channel, not only
        # public health: a wrong template must not accidentally look ready.
        sandbox.commands.run(
            START_COMMAND + " --check", user="root", envs=ROOT_COMMAND_ENV, timeout=15
        )
        state["phase"] = "ready"
        write_state(path, state)
        return state
    except BaseException:
        # Do not kill/delete data on failure. Best-effort pause, keeping the ID
        # and diagnostic logs local. Never auto-create a replacement on resume.
        if sandbox is not None:
            try:
                sandbox.pause(keep_memory=True)
            except Exception:
                pass
        raise


def resume(sdk, path: Path, timeout: int) -> dict:
    state = read_state(path)
    if state["phase"] != "ready":
        raise ValueError("Startup did not complete; inspect the existing sandbox before resuming")
    sandbox = sdk.connect(state["sandbox_id"], timeout=timeout)
    try:
        if public_origin(sandbox) != state["public_url"]:
            raise ValueError("Public origin changed; do not reuse the OAuth configuration")
        sandbox.commands.run(
            START_COMMAND + " --check", user="root", envs=ROOT_COMMAND_ENV, timeout=15
        )
        wait_ready(state["public_url"])
    except BaseException:
        try:
            sandbox.pause(keep_memory=True)
        except Exception:
            pass
        raise
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("create", help="Allocate one sandbox from an approved cold template")
    new.add_argument("--template", required=True, help="Your built E2B template ID or alias")
    for command in (new, commands.add_parser("resume")):
        command.add_argument("--timeout", type=int, default=3600, help="Seconds until auto-pause (60–3600)")
    commands.add_parser("status", help="Read provider status without waking a paused sandbox")
    commands.add_parser("pause", aliases=["stop"], help="Pause with RAM/files preserved; do not delete")
    delete = commands.add_parser("delete", help="Permanently destroy this sandbox and its data")
    delete.add_argument("--confirm-id", required=True, help="Type the exact sandbox ID to confirm deletion")
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        parser.error("Use Linux/macOS or WSL; native Windows lacks the secure state-file operations")
    if getattr(args, "timeout", 60) not in range(60, 3601):
        parser.error("--timeout must be 60–3600 seconds")
    if not os.environ.get("E2B_API_KEY"):
        parser.error("Set E2B_API_KEY securely in this terminal; never paste it into chat")
    try:
        from e2b import Sandbox
    except ImportError:
        parser.error("Install the optional launcher dependencies: pip install -r requirements-e2b.txt")
    try:
        if args.command == "create":
            state = create(Sandbox, args.state, args.template, args.timeout)
            show_ready(state, args.state)
        elif args.command == "resume":
            show_ready(resume(Sandbox, args.state, args.timeout), args.state)
        else:
            state = read_state(args.state)
            sandbox_id = state["sandbox_id"]
            if args.command == "status":
                info = Sandbox.get_info(sandbox_id)
                print("Sandbox: " + sandbox_id)
                print("State: " + str(info.state))
                print("Public origin: " + state["public_url"])
                print("Local startup phase: " + state["phase"])
            elif args.command in {"pause", "stop"}:
                Sandbox.pause(sandbox_id, keep_memory=True)
                print("Paused. Files, browser profile and RAM are retained in this sandbox.")
            elif args.command == "delete":
                if args.confirm_id != sandbox_id:
                    raise ValueError("Confirmation ID does not match")
                Sandbox.kill(sandbox_id)
                args.state.unlink()
                print("Sandbox permanently deleted. Its filesystem cannot be resumed.")
    except Exception as exc:
        # Provider/command exceptions can contain envs, credentials and log data.
        print(f"E2B operation failed ({type(exc).__name__}). No secrets printed.", file=sys.stderr)
        print("Keep your private state file. Check provider status before retrying; "
              "a timeout is not proof that allocation or deletion failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
