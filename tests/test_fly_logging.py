"""Regress the Fly root-bootstrap/non-root supervisor pipe-reopen failure."""

import configparser
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def fly_configuration():
    dockerfile = (ROOT / "Dockerfile").read_text()
    fly_stage = dockerfile.split("FROM desktop AS fly", 1)[1].split("FROM desktop AS local", 1)[0]
    # Execute the exact sed expressions in the Dockerfile against a temporary
    # copy, without Docker, root access, or modifications to the source config.
    expressions = re.findall(r"-e '([^']+)'", fly_stage)
    assert len(expressions) == 4
    arguments = ["sed"]
    for expression in expressions:
        arguments.extend(["-e", expression])
    result = subprocess.run(
        arguments, input=(ROOT / "docker/supervisord.conf").read_text(),
        capture_output=True, text=True, check=True,
    )
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(result.stdout)
    return config


def test_fly_child_logs_do_not_reopen_inherited_root_owned_pipes():
    fly = fly_configuration()
    base = configparser.ConfigParser(interpolation=None)
    base.read(ROOT / "docker/supervisord.conf")
    changed = set()
    for section in base.sections():
        for key, value in base[section].items():
            if fly[section][key] != value:
                changed.add((section, key))
        if section in {"program:display", "program:gateway"}:
            for stream in ("stdout", "stderr"):
                assert fly[section][f"{stream}_logfile"] == f"/tmp/%(program_name)s.{stream}.log"
                assert fly[section][f"{stream}_logfile_maxbytes"] == "1MB"
                assert fly[section][f"{stream}_logfile_backups"] == "2"
        else:
            assert dict(fly[section]) == dict(base[section])
    assert changed == {
        (section, key)
        for section in ("program:display", "program:gateway")
        for key in ("stdout_logfile", "stderr_logfile", "stdout_logfile_maxbytes", "stderr_logfile_maxbytes")
    }
    # Local behavior is preserved, and neither config asks supervisor to run root.
    assert base["program:gateway"]["stdout_logfile"] == "/dev/stdout"
    assert not any(fly[section].get("user") == "root" for section in fly.sections())


@pytest.mark.skipif(os.geteuid() == 0 or not Path("/proc/self/fd").exists(),
                    reason="Requires unprivileged Linux pipe permissions")
def test_inherited_fd_write_and_path_reopen_have_different_permissions():
    reader, writer = os.pipe()
    try:
        # Same permission mechanism as a root-owned Docker pipe after setuid:
        # no path-open permission, but the already-open write FD remains usable.
        os.fchmod(writer, 0)
        with pytest.raises(PermissionError):
            os.open(f"/proc/self/fd/{writer}", os.O_WRONLY)
        assert os.write(writer, b"ok") == 2
        assert os.read(reader, 2) == b"ok"
    finally:
        os.close(writer)
        os.close(reader)
