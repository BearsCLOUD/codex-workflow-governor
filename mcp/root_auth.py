"""Private authorization registry for local Workflow Governor MCP roots."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit


SCHEMA = "codex-workflow-mcp-roots.v1"
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
_SECRET = re.compile(r"(?i)(token|password|secret|api[_-]?key|oauth|bearer)")


class RootAuthorizationError(ValueError):
    """Raised when an MCP project root is not explicitly and currently authorized."""


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)


def _require_owner(path: Path, *, kind: str | None = None, mode: int | None = None) -> os.stat_result:
    try:
        value = path.lstat()
    except OSError as exc:
        raise RootAuthorizationError(f"missing protected path: {path}") from exc
    if stat.S_ISLNK(value.st_mode):
        raise RootAuthorizationError(f"protected path must not be a symlink: {path}")
    if value.st_uid != os.geteuid():
        raise RootAuthorizationError(f"protected path is not owned by the current user: {path}")
    if kind == "dir" and not stat.S_ISDIR(value.st_mode):
        raise RootAuthorizationError(f"protected path must be a directory: {path}")
    if kind == "file" and not stat.S_ISREG(value.st_mode):
        raise RootAuthorizationError(f"protected path must be a regular file: {path}")
    if mode is not None and stat.S_IMODE(value.st_mode) != mode:
        raise RootAuthorizationError(f"protected path must have mode {mode:04o}: {path}")
    return value


def _reject_symlink_components(path: Path) -> None:
    absolute = path if path.is_absolute() else path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            value = current.lstat()
        except OSError as exc:
            raise RootAuthorizationError(f"path component is missing: {current}") from exc
        if stat.S_ISLNK(value.st_mode):
            raise RootAuthorizationError(f"path component must not be a symlink: {current}")


def _git(project: Path, *arguments: str, optional: bool = False) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(project), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
        text=True,
        env={
            "HOME": os.environ.get("HOME", str(Path.home())),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        },
    )
    if completed.returncode:
        if optional:
            return None
        raise RootAuthorizationError("project root is not an accessible Git worktree")
    return completed.stdout.strip()


def _path_identity(path: Path, label: str, *, regular_file: bool = False) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    _reject_symlink_components(resolved)
    value = _require_owner(resolved, kind="file" if regular_file else "dir")
    return {
        "label": label,
        "path": str(resolved),
        "device": value.st_dev,
        "inode": value.st_ino,
    }


def _origin_digest(project: Path) -> str | None:
    origin = _git(project, "remote", "get-url", "origin", optional=True)
    if not origin:
        return None
    if "\n" in origin or "\r" in origin or _SECRET.search(origin):
        raise RootAuthorizationError("origin URL contains forbidden credential-like material")
    if "://" in origin:
        parsed = urlsplit(origin)
        if parsed.username is not None or parsed.password is not None:
            raise RootAuthorizationError("origin URL userinfo is not allowed")
        normalized = f"{parsed.scheme.lower()}://{(parsed.hostname or '').lower()}"
        if parsed.port is not None:
            normalized += f":{parsed.port}"
        normalized += parsed.path.rstrip("/")
    elif re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:.+", origin):
        user_host, path = origin.split(":", 1)
        user, host = user_host.split("@", 1)
        normalized = f"{user.lower()}@{host.lower()}:{path.rstrip('/')}"
    else:
        normalized = origin.rstrip("/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def project_identity(project_root: str | Path) -> dict[str, Any]:
    supplied = Path(project_root).expanduser()
    if not supplied.is_absolute():
        raise RootAuthorizationError("project root must be an absolute path")
    _reject_symlink_components(supplied)
    project = supplied.resolve(strict=True)
    if project != supplied:
        raise RootAuthorizationError("project root must already be canonical")
    root = _path_identity(project, "project_root")
    top = _git(project, "rev-parse", "--show-toplevel")
    if top is None or Path(top).resolve(strict=True) != project:
        raise RootAuthorizationError("project root must exactly equal the Git worktree top-level")
    git_dir_raw = _git(project, "rev-parse", "--absolute-git-dir")
    if not git_dir_raw:
        raise RootAuthorizationError("Git directory is unavailable")
    git_dir = _path_identity(Path(git_dir_raw), "git_dir")
    common_raw = _git(project, "rev-parse", "--git-common-dir")
    if not common_raw:
        raise RootAuthorizationError("Git common directory is unavailable")
    common_path = Path(common_raw)
    if not common_path.is_absolute():
        common_path = project / common_path
    common_dir = _path_identity(common_path, "git_common_dir")
    gitfile: dict[str, Any] | None = None
    marker = project / ".git"
    marker_stat = marker.lstat()
    if stat.S_ISLNK(marker_stat.st_mode):
        raise RootAuthorizationError("worktree .git marker must not be a symlink")
    if stat.S_ISREG(marker_stat.st_mode):
        gitfile = _path_identity(marker, "gitfile", regular_file=True)
    elif not stat.S_ISDIR(marker_stat.st_mode):
        raise RootAuthorizationError("worktree .git marker is missing")
    return {
        "schema_version": "codex-workflow-mcp-root-identity.v1",
        "project_root": root,
        "git_top_level": str(project),
        "git_dir": git_dir,
        "git_common_dir": common_dir,
        "gitfile": gitfile,
        "origin_sha256": _origin_digest(project),
    }


def identity_digest(identity: dict[str, Any]) -> str:
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _codex_home() -> Path:
    configured = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    resolved = configured.resolve(strict=True)
    _require_owner(resolved, kind="dir")
    return resolved


def _registry_dir(*, create: bool) -> Path:
    directory = _codex_home() / "workflow-governor-mcp"
    if create and not directory.exists():
        directory.mkdir(mode=PRIVATE_DIR_MODE)
    _require_owner(directory, kind="dir", mode=PRIVATE_DIR_MODE)
    return directory


def _registry_path(*, create: bool) -> Path:
    return _registry_dir(create=create) / "authorized-roots.json"


def _empty_registry() -> dict[str, Any]:
    return {"schema_version": SCHEMA, "roots": {}}


def _read_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_registry()
    _require_owner(path, kind="file", mode=PRIVATE_FILE_MODE)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RootAuthorizationError("root registry is malformed") from exc
    if not isinstance(value, dict) or set(value) != {"schema_version", "roots"}:
        raise RootAuthorizationError("root registry has an invalid object shape")
    if value["schema_version"] != SCHEMA or not isinstance(value["roots"], dict):
        raise RootAuthorizationError("root registry has an unsupported schema")
    return value


def _write_registry(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".authorized-roots.", dir=path.parent)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


@contextlib.contextmanager
def _registry_lock(*, create: bool) -> Iterator[Path]:
    directory = _registry_dir(create=create)
    lock = directory / "registry.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock, flags, PRIVATE_FILE_MODE)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        _require_owner(lock, kind="file", mode=PRIVATE_FILE_MODE)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield directory / "authorized-roots.json"
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def authorize(project_root: str | Path) -> dict[str, Any]:
    identity = project_identity(project_root)
    project = identity["git_top_level"]
    with _registry_lock(create=True) as path:
        registry = _read_registry(path)
        registry["roots"][project] = {
            "identity": identity,
            "identity_sha256": identity_digest(identity),
        }
        _write_registry(path, registry)
    return {"project_root": project, "identity_sha256": identity_digest(identity)}


def revoke(project_root: str | Path) -> bool:
    project = str(Path(project_root).expanduser().resolve(strict=False))
    with _registry_lock(create=True) as path:
        registry = _read_registry(path)
        removed = registry["roots"].pop(project, None) is not None
        _write_registry(path, registry)
    return removed


def list_authorized() -> list[dict[str, str]]:
    with _registry_lock(create=True) as path:
        registry = _read_registry(path)
    return [
        {"project_root": project, "identity_sha256": str(entry.get("identity_sha256", ""))}
        for project, entry in sorted(registry["roots"].items())
    ]


@dataclass
class AuthorizedRoot:
    project: Path
    identity: dict[str, Any]
    identity_sha256: str
    descriptors: list[int]

    def verify(self) -> None:
        current = project_identity(self.project)
        if current != self.identity or identity_digest(current) != self.identity_sha256:
            raise RootAuthorizationError("authorized project identity has drifted")
        paths = [self.identity["project_root"], self.identity["git_dir"], self.identity["git_common_dir"]]
        if self.identity.get("gitfile"):
            paths.append(self.identity["gitfile"])
        for descriptor, expected in zip(self.descriptors, paths):
            value = os.fstat(descriptor)
            if value.st_dev != expected["device"] or value.st_ino != expected["inode"]:
                raise RootAuthorizationError("authorized project descriptor identity has drifted")

    def close(self) -> None:
        for descriptor in self.descriptors:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        self.descriptors.clear()

    def __enter__(self) -> "AuthorizedRoot":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def open_authorized(project_root: str | Path) -> AuthorizedRoot:
    project = str(Path(project_root).expanduser())
    if not Path(project).is_absolute():
        raise RootAuthorizationError("project root must be absolute")
    with _registry_lock(create=True) as path:
        registry = _read_registry(path)
        entry = registry["roots"].get(project)
    if not isinstance(entry, dict) or set(entry) != {"identity", "identity_sha256"}:
        raise RootAuthorizationError("project root is not authorized for local MCP")
    identity = entry["identity"]
    if not isinstance(identity, dict) or entry["identity_sha256"] != identity_digest(identity):
        raise RootAuthorizationError("authorized root registry entry is corrupt")
    current = project_identity(project)
    if current != identity:
        raise RootAuthorizationError("authorized project identity has drifted")
    paths = [identity["project_root"], identity["git_dir"], identity["git_common_dir"]]
    if identity.get("gitfile"):
        paths.append(identity["gitfile"])
    descriptors: list[int] = []
    try:
        for item in paths:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            if item["label"] != "gitfile":
                flags |= os.O_DIRECTORY
            descriptors.append(os.open(item["path"], flags))
        result = AuthorizedRoot(Path(project), identity, str(entry["identity_sha256"]), descriptors)
        result.verify()
        return result
    except Exception:
        for descriptor in descriptors:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        raise
