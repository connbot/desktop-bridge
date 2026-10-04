import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fly_entrypoint", ROOT / "docker/fly-entrypoint.py")
entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrypoint)


def test_prepare_creates_only_required_directories(tmp_path):
    entrypoint.prepare_data(tmp_path, os.getuid(), os.getgid())
    assert sorted(path.name for path in tmp_path.iterdir()) == ["profile", "state", "workspace"]
    assert all(path.is_dir() for path in tmp_path.iterdir())


def test_prepare_does_not_recursively_touch_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    nested = workspace / "keep.txt"
    nested.write_text("unchanged")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (workspace / "link").symlink_to(outside, target_is_directory=True)
    seen = []
    monkeypatch.setattr(entrypoint.os, "fchown", lambda fd, uid, gid: seen.append(os.fstat(fd)))
    entrypoint.prepare_data(tmp_path, os.getuid(), os.getgid())
    assert len(seen) == 4  # Mount root plus three fixed directories, never their contents.
    assert nested.read_text() == "unchanged"
    assert (workspace / "link").is_symlink()
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("kind", ["file", "symlink"])
@pytest.mark.parametrize("name", ["workspace", "state", "profile"])
def test_prepare_rejects_non_directory_or_symlink_children(tmp_path, name, kind):
    path = tmp_path / name
    if kind == "file":
        path.write_text("keep")
    else:
        target = tmp_path / "outside"
        target.mkdir()
        path.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        entrypoint.prepare_data(tmp_path, os.getuid(), os.getgid())
    if kind == "file":
        assert path.read_text() == "keep"
    else:
        assert path.is_symlink()
        assert list(target.iterdir()) == []


def test_prepare_rejects_symlink_mount_root(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        entrypoint.prepare_data(link, os.getuid(), os.getgid())
    assert list(target.iterdir()) == []


def test_prepare_rejects_regular_file_mount_root(tmp_path):
    target = tmp_path / "target"
    target.write_text("keep")
    with pytest.raises(OSError):
        entrypoint.prepare_data(target, os.getuid(), os.getgid())
    assert target.read_text() == "keep"


def fake_root(monkeypatch):
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 0)
    monkeypatch.setattr(entrypoint.pwd, "getpwnam", lambda name: SimpleNamespace(
        pw_name="bridge", pw_uid=1000, pw_gid=1000, pw_dir="/home/bridge"
    ))
    monkeypatch.setenv("BRIDGE_DATA", "/data")


def test_privileges_drop_before_exec_without_recursive_chown(monkeypatch):
    fake_root(monkeypatch)
    events = []
    monkeypatch.setattr(entrypoint, "prepare_data", lambda *args: events.append(("prepare", args)))
    monkeypatch.setattr(entrypoint.os, "initgroups", lambda *args: events.append(("groups", args)))
    monkeypatch.setattr(entrypoint.os, "setgid", lambda *args: events.append(("gid", args)))
    monkeypatch.setattr(entrypoint.os, "setuid", lambda *args: events.append(("uid", args)))
    monkeypatch.setattr(entrypoint.os, "umask", lambda *args: events.append(("umask", args)))
    monkeypatch.setattr(entrypoint.os, "execvp", lambda *args: events.append(("exec", args)))
    monkeypatch.setenv("HOME", "/root")
    entrypoint.main(["supervisor", "-c", "config"])
    assert [event[0] for event in events] == ["prepare", "groups", "gid", "uid", "umask", "exec"]
    assert events[2][1] == (1000,)
    assert events[3][1] == (1000,)
    assert events[-1][1] == ("supervisor", ["supervisor", "-c", "config"])
    assert os.environ["HOME"] == "/home/bridge"


def test_failed_privilege_drop_never_executes(monkeypatch):
    fake_root(monkeypatch)
    monkeypatch.setattr(entrypoint, "prepare_data", lambda *args: None)
    monkeypatch.setattr(entrypoint.os, "initgroups", lambda *args: None)
    monkeypatch.setattr(entrypoint.os, "setgid", lambda *args: None)
    def fail_uid(uid):
        raise PermissionError("cannot drop")
    monkeypatch.setattr(entrypoint.os, "setuid", fail_uid)
    monkeypatch.setattr(entrypoint.os, "execvp", lambda *args: pytest.fail("exec before drop"))
    with pytest.raises(PermissionError):
        entrypoint.main(["supervisor"])


def test_non_root_and_custom_mount_fail_closed(monkeypatch):
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 1000)
    with pytest.raises(SystemExit):
        entrypoint.main(["supervisor"])
    fake_root(monkeypatch)
    monkeypatch.setenv("BRIDGE_DATA", "/different")
    with pytest.raises(SystemExit):
        entrypoint.main(["supervisor"])


def test_empty_command_rejected():
    with pytest.raises(SystemExit):
        entrypoint.main([])
