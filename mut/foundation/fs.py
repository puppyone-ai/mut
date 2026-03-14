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
    try:
        os.write(fd, data)
        os.close(fd)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass  # already closed
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


# ── Async wrappers (for server-side use with asyncio) ──────────

import asyncio


async def async_read_json(path: Path):
    return await asyncio.to_thread(read_json, path)


async def async_write_json(path: Path, data, *, indent: int = 2):
    await asyncio.to_thread(write_json, path, data, indent=indent)


async def async_read_text(path: Path) -> str:
    return await asyncio.to_thread(read_text, path)


async def async_write_text(path: Path, text: str):
    await asyncio.to_thread(write_text, path, text)


async def async_atomic_write(path: Path, data: bytes):
    await asyncio.to_thread(atomic_write, path, data)


async def async_mkdir_p(path: Path):
    await asyncio.to_thread(mkdir_p, path)


async def async_rmtree(path: Path):
    await asyncio.to_thread(rmtree, path)


async def async_read_bytes(path: Path) -> bytes:
    return await asyncio.to_thread(path.read_bytes)


async def async_write_bytes(path: Path, data: bytes):
    def _write():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    await asyncio.to_thread(_write)


async def async_exists(path: Path) -> bool:
    return await asyncio.to_thread(path.exists)


async def async_unlink(path: Path):
    def _unlink():
        if path.exists():
            path.unlink()
    await asyncio.to_thread(_unlink)


async def async_iterdir(path: Path) -> list:
    """Return sorted list of children (avoids async iteration issues)."""
    def _iter():
        if not path.exists():
            return []
        return sorted(path.iterdir())
    return await asyncio.to_thread(_iter)
