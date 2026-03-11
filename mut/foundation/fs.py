"""Filesystem utilities: atomic writes, locks, directory helpers."""

import json
import os
import shutil
import tempfile
from pathlib import Path


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data, *, indent: int = 2):
    atomic_write(path, json.dumps(data, indent=indent, ensure_ascii=False).encode("utf-8"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def write_text(path: Path, text: str):
    atomic_write(path, text.encode("utf-8"))


def atomic_write(path: Path, data: bytes):
    """Write data to path atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    closed = False
    try:
        os.write(fd, data)
        os.close(fd)
        closed = True
        os.replace(tmp, str(path))
    except BaseException:
        if not closed:
            os.close(fd)
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def mkdir_p(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def rmtree(path: Path):
    if path.is_dir():
        shutil.rmtree(path)
    elif path.is_file():
        path.unlink()


def lock_acquire(lock_path: Path) -> bool:
    """Acquire an exclusive file lock (atomic create). Returns True on success.

    If a stale lock from a dead process exists, it is automatically removed.
    """
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        if _is_stale_lock(lock_path):
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            return lock_acquire(lock_path)
        return False


def _is_stale_lock(lock_path: Path) -> bool:
    """Check if the PID in the lock file is still alive."""
    try:
        pid = int(lock_path.read_text().strip())
        os.kill(pid, 0)
        return False
    except (ValueError, ProcessLookupError):
        return True
    except PermissionError:
        return False
    except FileNotFoundError:
        return True


def lock_release(lock_path: Path):
    """Release a file lock."""
    if lock_path.exists():
        lock_path.unlink()



def is_safe_path(base: Path, target: Path) -> bool:
    """Ensure target resolves within base (prevents path traversal via '..')."""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False
