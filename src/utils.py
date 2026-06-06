"""
utils.py — Foundational utilities for the Windows backup system.
No imports from other src/ modules (no circular deps).
stdlib only: os, shutil, ctypes, json, tempfile, pathlib, logging
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_drives() -> list[str]:
    """Return a list of drive root paths present on this Windows machine."""
    drives: list[str] = []
    bitmask: int = ctypes.windll.kernel32.GetLogicalDrives()
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if bitmask & 1:
            drives.append(f"{letter}:\\")
        bitmask >>= 1
    return drives


def _bytes_to_gb(n: int) -> float:
    return round(n / (1024 ** 3), 2)


def _tb_to_bytes(tb: float) -> int:
    return int(tb * (1024 ** 4))


# ---------------------------------------------------------------------------
# Disk scanning
# ---------------------------------------------------------------------------

def scan_large_disks(min_size_tb: float = 2.0) -> list[dict]:
    """
    Enumerate all logical drives and return those whose total capacity is
    >= *min_size_tb* terabytes.

    Returns a list of dicts::

        [{"path": "D:\\", "total_gb": float, "free_gb": float, "used_gb": float}, ...]

    Drives that raise PermissionError / OSError are silently skipped.
    """
    min_bytes = _tb_to_bytes(min_size_tb)
    result: list[dict] = []

    for drive in _get_drives():
        try:
            usage = shutil.disk_usage(drive)
        except (PermissionError, OSError):
            logger.debug("Skipping drive %s (access error)", drive)
            continue

        if usage.total >= min_bytes:
            result.append(
                {
                    "path": drive,
                    "total_gb": _bytes_to_gb(usage.total),
                    "free_gb": _bytes_to_gb(usage.free),
                    "used_gb": _bytes_to_gb(usage.used),
                }
            )

    return result


def get_target_disk(
    min_size_tb: float = 2.0,
    min_free_gb: float = 100.0,
) -> str | None:
    """
    Return the path of the first large disk that has at least *min_free_gb*
    GB of free space, or None if no disk qualifies.
    """
    for disk in scan_large_disks(min_size_tb):
        if disk["free_gb"] >= min_free_gb:
            return disk["path"]
    return None


# ---------------------------------------------------------------------------
# PID-file locking
# ---------------------------------------------------------------------------

def _pid_is_alive(pid: int) -> bool:
    """Return True if a process with *pid* is currently running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        import errno as _errno
        if exc.errno == _errno.ESRCH:
            return False  # No such process
        # EPERM → process exists but not owned by us → treat as alive
        return True


def _read_pid(pid_file: str) -> int | None:
    """Read and return the integer PID stored in *pid_file*, or None."""
    try:
        with open(pid_file, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def acquire_lock(pid_file: str = "backup.pid") -> bool:
    """
    Try to acquire an exclusive lock backed by *pid_file*.

    * If the file exists and the PID inside belongs to a live process →
      return False (already running).
    * If the file is missing, stale (dead PID), or unreadable → write the
      current PID and return True.
    """
    existing_pid = _read_pid(pid_file)
    if existing_pid is not None and _pid_is_alive(existing_pid):
        logger.warning(
            "acquire_lock: backup already running (PID %d, lock=%s)",
            existing_pid,
            pid_file,
        )
        return False

    # File missing, unreadable, or stale — (re)claim it
    try:
        with open(pid_file, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        logger.debug("acquire_lock: acquired (PID %d, lock=%s)", os.getpid(), pid_file)
        return True
    except OSError as exc:
        logger.error("acquire_lock: could not write PID file %s: %s", pid_file, exc)
        return False


def release_lock(pid_file: str = "backup.pid") -> None:
    """
    Delete *pid_file* if it contains our own PID.
    No-op if the file is missing or belongs to another process.
    """
    existing_pid = _read_pid(pid_file)
    if existing_pid != os.getpid():
        logger.debug(
            "release_lock: PID mismatch (file=%s, ours=%d) — skipping",
            pid_file,
            os.getpid(),
        )
        return

    try:
        os.remove(pid_file)
        logger.debug("release_lock: removed %s", pid_file)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.error("release_lock: could not remove %s: %s", pid_file, exc)


# ---------------------------------------------------------------------------
# Status file I/O
# ---------------------------------------------------------------------------

def write_status(data: dict, path: str) -> None:
    """
    Atomically write *data* as JSON to *path*.

    Uses a temp file in the same directory + os.replace() so readers never
    see a partial write.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(dest))
        logger.debug("write_status: wrote %s", path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def read_status(path: str) -> dict:
    """
    Read JSON from *path* and return it as a dict.
    Returns an empty dict if the file is missing, empty, or malformed.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
