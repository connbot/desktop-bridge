#!/usr/bin/env python3
"""E2B-only root bootstrap: close auxiliary ingress before starting any desktop."""

from __future__ import annotations

import os
import pwd
import runpy
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

CHAIN = "BRIDGE_E2B_INPUT"
LATCH = Path("/run/agent-computer-e2b-secured")
PREPARED = Path("/opt/agent-computer/template-ready")


def firewall_commands(binary: str) -> list[list[str]]:
    # E2B forwards even loopback listeners onto eth0. INPUT therefore needs a
    # guest-wide allowlist; a localhost bind is NOT an ingress security boundary.
    return [
        [binary, "-w", "-N", CHAIN],
        [binary, "-w", "-A", CHAIN, "-i", "lo", "-j", "ACCEPT"],
        [binary, "-w", "-A", CHAIN, "-m", "conntrack", "--ctstate",
         "ESTABLISHED,RELATED", "-j", "ACCEPT"],
        [binary, "-w", "-A", CHAIN, "-p", "tcp", "-m", "multiport", "--dports",
         "8080,49983", "-j", "ACCEPT"],
        [binary, "-w", "-A", CHAIN, "-p", "tcp", "-j", "DROP"],
        [binary, "-w", "-A", CHAIN, "-j", "RETURN"],
        [binary, "-w", "-I", "INPUT", "1", "-j", CHAIN],
    ]


def secure_ingress(run=subprocess.run) -> None:
    # Legacy xtables is built into E2B's published kernel configurations. Both
    # families must succeed; never launch publicly with a half-applied firewall.
    # Do not flush provider rules or touch OUTPUT, FORWARD, routing or NAT.
    for binary in ("/usr/sbin/iptables-legacy", "/usr/sbin/ip6tables-legacy"):
        for command in firewall_commands(binary):
            run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def verify_ingress(run=subprocess.run) -> None:
    for binary in ("/usr/sbin/iptables-legacy", "/usr/sbin/ip6tables-legacy"):
        rules = run([binary, "-w", "-S", CHAIN], check=True, capture_output=True,
                    text=True).stdout.splitlines()
        expected = firewall_commands(binary)[1:-1]
        if len(rules) != len(expected) + 1 or rules[0] != "-N " + CHAIN:
            raise RuntimeError("Unexpected E2B firewall rules")
        # -S may reorder conntrack states when rendering. Ask xtables itself to
        # compare rules instead of assuming its text order matches our input.
        for command in expected:
            run([binary, "-w", "-C", *command[3:]], check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        inputs = run([binary, "-w", "-S", "INPUT"], check=True, capture_output=True,
                     text=True).stdout.splitlines()
        jumps = [line for line in inputs if line.startswith("-A ")]
        if not jumps or jumps[0] != "-A INPUT -j " + CHAIN:
            raise RuntimeError("E2B firewall must be the first INPUT rule")


def prepare_template() -> None:
    # E2B finalize makes /usr/local writable AFTER Docker/build instructions.
    # The ready command runs later. It uses Debian Python in isolated/no-site
    # mode, never /usr/local Python or its sitecustomize. No app/secret starts.
    subprocess.run(["/usr/bin/chmod", "-R", "go-w", "/usr/local"], check=True)
    PREPARED.write_text("Cold template; /usr/local hardened after E2B finalize\n")
    PREPARED.chmod(0o600)


def verify_template() -> None:
    if not PREPARED.is_file():
        raise RuntimeError("Template hardening did not run")
    for path in ("/opt", "/opt/agent-computer", "/opt/agent-computer/e2b-entrypoint.py",
                 "/usr/local", "/usr/local/bin", "/usr/local/bin/python3"):
        info = Path(path).stat()
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            raise RuntimeError("Unsafe template ownership or permissions")


def runtime_environment(environ: dict) -> dict:
    owner = environ.get("BRIDGE_OWNER_TOKEN", "")
    public = environ.get("BRIDGE_PUBLIC_URL", "")
    url = urlsplit(public)
    if not 32 <= len(owner) <= 256 or not owner.strip():
        raise ValueError("Missing fresh owner token")
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment or url.port is not None):
        raise ValueError("Expected the exact public HTTPS origin")
    # Deliberate allowlist: the provider API key, envd credentials, template
    # build variables, and arbitrary host secrets must never reach the desktop.
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/home/bridge",
        "LANG": "C.UTF-8",
        "DISPLAY": ":99",
        "PYTHONUNBUFFERED": "1",
        "BRIDGE_DATA": "/data",
        "BRIDGE_OWNER_TOKEN": owner,
        "BRIDGE_PUBLIC_URL": public,
        "BRIDGE_CODING_PERMISSION_MODE": "safe",
    }


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("E2B bootstrap must start as root")
    if sys.argv[1:] == ["--prepare-template"]:
        prepare_template()
        return
    verify_template()
    if sys.argv[1:] == ["--check"]:
        verify_ingress()
        if not LATCH.is_file():
            raise RuntimeError("Startup did not install its readiness latch")
        return
    if sys.argv[1:]:
        raise SystemExit("Unknown bootstrap option")
    env = runtime_environment(dict(os.environ))
    account = pwd.getpwnam("bridge")
    if account.pw_uid == 0 or LATCH.exists():
        raise SystemExit("Refusing an unsafe or repeated bootstrap")
    os.umask(0o077)
    # Reuse the reviewed non-recursive, no-symlink directory initializer.
    prepare = runpy.run_path("/opt/agent-computer/fly-entrypoint.py")["prepare_data"]
    prepare(Path("/data"), account.pw_uid, account.pw_gid)
    secure_ingress()
    verify_ingress()
    LATCH.write_text("IPv4 and IPv6 INPUT allowlists installed before desktop startup\n")
    # E2B may provision passwordless root/default-user sudo. Clearing capabilities
    # and no_new_privs prevents any desktop child regaining root via su/sudo/setuid.
    # These restrictions apply to this process tree, not envd's control channel.
    command = [
        "/usr/bin/setpriv", f"--reuid={account.pw_uid}", f"--regid={account.pw_gid}",
        "--clear-groups", "--no-new-privs", "--bounding-set=-all", "--inh-caps=-all",
        "--ambient-caps=-all", "--", "/usr/bin/supervisord", "-c",
        "/etc/supervisor/supervisord.conf",
    ]
    os.execve(command[0], command, env)


if __name__ == "__main__":
    main()
