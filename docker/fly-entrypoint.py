#!/usr/bin/env python3
"""Initialize a fresh Fly mount, then permanently drop to the bridge user.

Only the Fly build target starts this bootstrap as root. Never follow symlinks,
recursively chown a workspace, execute workspace code, or remain root at runtime.
"""

from __future__ import annotations

import os
import pwd
import sys
from pathlib import Path

DATA_DIR = Path("/data")
DIRECTORIES = ("workspace", "state", "profile")


def prepare_data(root: Path, uid: int, gid: int) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(root, flags)
    try:
        os.fchown(root_fd, uid, gid)
        for name in DIRECTORIES:
            try:
                os.mkdir(name, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            directory_fd = os.open(name, flags, dir_fd=root_fd)
            try:
                os.fchown(directory_fd, uid, gid)
            finally:
                os.close(directory_fd)
    finally:
        os.close(root_fd)


def main(argv: list[str] | None = None) -> None:
    command = sys.argv[1:] if argv is None else argv
    if not command:
        raise SystemExit("Runtime command is required")
    if os.geteuid() != 0:
        raise SystemExit("Fly volume bootstrap must start as root and drops privileges before exec")
    if os.environ.get("BRIDGE_DATA", "/data") != "/data":
        raise SystemExit("Fly volume bootstrap requires BRIDGE_DATA=/data")
    account = pwd.getpwnam("bridge")
    if account.pw_uid == 0:
        raise SystemExit("The bridge runtime user must not be root")
    prepare_data(DATA_DIR, account.pw_uid, account.pw_gid)
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    os.umask(0o077)
    os.environ["HOME"] = account.pw_dir
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
