"""Portable personal context, with bounded data and revision-checked atomic writes.

This is an editable notebook for the connected model, not another agent loop.
The existing runtime lease serializes owner and model writes. A file lock also
protects cooperating store instances. Container shell access remains trusted.
"""
from __future__ import annotations

import fcntl
import json
import os
import stat
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .state import BridgeError

MAX_CONTEXT_BYTES = 128 * 1024


class PersonalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Profile(PersonalModel):
    name: str = Field(default="", max_length=80)
    preferences: str = Field(default="", max_length=4000)
    goals: str = Field(default="", max_length=4000)
    constraints: str = Field(default="", max_length=4000)


def relative_artifact(path: str) -> str:
    parts = PurePosixPath(path).parts
    if (
        not path or len(path) > 500 or "\\" in path or "\x00" in path
        or path.startswith("/") or any(p in {".", ".."} for p in path.split("/"))
        or not parts or parts[0] == "personal" or "//" in path
    ):
        raise ValueError("Use a workspace-relative artifact path outside personal/, without traversal")
    return path


class Task(PersonalModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=160)
    status: Literal["planned", "in_progress", "needs_input", "completed", "cancelled"] = "planned"
    summary: str = Field(default="", max_length=2000)
    next_step: str = Field(default="", max_length=2000)
    evidence: str = Field(default="", max_length=2000)
    artifacts: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("artifacts")
    @classmethod
    def safe_artifacts(cls, paths):
        return list(dict.fromkeys(relative_artifact(path) for path in paths))


class SavedTask(Task):
    updated_at: str = Field(default="", max_length=40)
    updated_by: Literal["owner", "agent"] = "owner"


class Context(PersonalModel):
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    profile: Profile = Field(default_factory=Profile)
    tasks: list[SavedTask] = Field(default_factory=list, max_length=100)
    updated_at: str = Field(default="", max_length=40)
    updated_by: Literal["owner", "agent"] = "owner"

    @field_validator("tasks")
    @classmethod
    def unique_tasks(cls, tasks):
        if any(task.status == "completed" and not task.evidence.strip() and not task.artifacts for task in tasks):
            raise ValueError("Completed tasks require evidence or artifact references")
        if len({task.id for task in tasks}) != len(tasks):
            raise ValueError("Task IDs must be unique")
        return tasks


class ProfileUpdate(PersonalModel):
    expected_revision: int = Field(ge=0)
    profile: Profile


class TaskUpdate(PersonalModel):
    expected_revision: int = Field(ge=0)
    task: Task


class ContextImport(PersonalModel):
    expected_revision: int = Field(ge=0)
    context: Context


class PersonalStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.directory = self.workspace / "personal"

    @contextmanager
    def locked(self, *, create=False):
        """Fixed dir-relative filenames and O_NOFOLLOW reject redirected state."""
        directory_fd = lock_fd = None
        try:
            if not self.directory.exists() and not self.directory.is_symlink():
                if not create:
                    yield None
                    return
                self.directory.mkdir(mode=0o700, exist_ok=True)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            lock_fd = os.open(
                ".context.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600,
                dir_fd=directory_fd,
            )
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                raise BridgeError("UNSAFE_CONTEXT_PATH", "Personal context lock must be a regular file")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield directory_fd
        except OSError as exc:
            raise BridgeError("CONTEXT_STORAGE_ERROR", "Personal context storage is unavailable or unsafe") from exc
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
            if directory_fd is not None:
                os.close(directory_fd)

    def _read(self, directory_fd) -> Context:
        if directory_fd is None:
            return Context()
        try:
            fd = os.open("context.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        except FileNotFoundError:
            return Context()
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONTEXT_BYTES:
                raise BridgeError("INVALID_CONTEXT", "Context must be a regular JSON file up to 128 KiB")
            raw = source.read(MAX_CONTEXT_BYTES + 1)
        if len(raw) > MAX_CONTEXT_BYTES:
            raise BridgeError("INVALID_CONTEXT", "Context exceeds 128 KiB")
        try:
            return Context.model_validate(json.loads(raw))
        except ValueError as exc:
            raise BridgeError("INVALID_CONTEXT", "Stored context is invalid; restore a valid export") from exc

    def read(self) -> dict:
        with self.locked() as directory_fd:
            return self._read(directory_fd).model_dump()

    def artifact(self, path: str) -> dict:
        relative_artifact(path)
        target = self.workspace / path
        # References never serve directories, symlinks, or files outside the workspace.
        safe = all(not part.is_symlink() for part in [target, *target.parents]
                   if part != self.workspace and part.is_relative_to(self.workspace))
        try:
            available = safe and target.is_file() and target.resolve().is_relative_to(self.workspace)
            size = target.stat().st_size if available else None
        except OSError:
            available, size = False, None
        return {"path": path, "available": available, "bytes": size}

    def view_data(self, data: dict) -> dict:
        for task in data["tasks"]:
            task["artifact_details"] = [self.artifact(path) for path in task["artifacts"]]
        return data

    def view(self) -> dict:
        return self.view_data(self.read())

    def prepare(self, current: Context, expected_revision: int, *, profile=None, task=None, imported=None, actor="owner") -> dict:
        current = current.model_copy(deep=True)
        if current.revision != expected_revision:
            raise BridgeError("CONTEXT_CONFLICT", "Context changed. Read it again before saving; your edit was not applied")
        now = datetime.now(UTC).isoformat()
        if imported is not None:
            current = imported.model_copy(deep=True)
        if profile is not None:
            current.profile = profile
        if task is not None:
            if task.status == "completed" and not task.evidence.strip() and not task.artifacts:
                raise BridgeError("EVIDENCE_REQUIRED", "Add evidence or a real artifact before reporting completion")
            for path in task.artifacts:
                if not self.artifact(path)["available"]:
                    raise BridgeError("ARTIFACT_MISSING", f"Save the artifact with Coding Tools MCP first: {path}")
            saved = SavedTask(**task.model_dump(), updated_at=now, updated_by=actor)
            current.tasks = [saved, *[old for old in current.tasks if old.id != task.id]]
        current.revision = expected_revision + 1
        current.updated_at, current.updated_by = now, actor
        # Revalidate collection limits after mutation and before replacing anything.
        try:
            data = Context.model_validate(current.model_dump()).model_dump()
        except ValidationError as exc:
            raise BridgeError("INVALID_CONTEXT", "Context exceeds the limit of 100 tasks or contains invalid records") from exc
        encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
        if len(encoded) > MAX_CONTEXT_BYTES:
            raise BridgeError("CONTEXT_TOO_LARGE", "Personal context is limited to 128 KiB; remove old tasks before saving")
        return data

    def update(self, expected_revision: int, *, profile=None, task=None, imported=None, actor="owner") -> dict:
        with self.locked(create=True) as directory_fd:
            current = self._read(directory_fd)
            data = self.prepare(current, expected_revision, profile=profile, task=task, imported=imported, actor=actor)
            encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
            # Refuse to replace a symlink even though replace would only unlink it.
            try:
                info = os.stat("context.json", dir_fd=directory_fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise BridgeError("UNSAFE_CONTEXT_PATH", "Personal context must be a regular file")
            except FileNotFoundError:
                pass
            temp = f".context-{uuid.uuid4().hex}.tmp"
            try:
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                             dir_fd=directory_fd)
                with os.fdopen(fd, "wb") as target:
                    target.write(encoded)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temp, "context.json", src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                os.fsync(directory_fd)
            finally:
                try:
                    os.unlink(temp, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
            return data


class AgentProfileUpdate(ProfileUpdate):
    action_id: str = Field(min_length=1, max_length=128)


class AgentTaskUpdate(TaskUpdate):
    action_id: str = Field(min_length=1, max_length=128)
