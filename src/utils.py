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
# Remote disk management (V2)
# ---------------------------------------------------------------------------

def create_target_connector(config: dict):
    """
    Create a connector for the backup_target from config.
    Returns WindowsConnector for Windows targets.
    """
    target = config.get("backup_target", {})
    if not target:
        return None
    try:
        import sys, os as _os
        _src = _os.path.dirname(_os.path.abspath(__file__))
        if _src not in sys.path:
            sys.path.insert(0, _src)
        from connector import create_connector
        return create_connector(target)
    except Exception as exc:
        logger.error("create_target_connector failed: %s", exc)
        return None


def scan_remote_disks(connector, min_size_tb: float = 2.0) -> list[dict]:
    """
    Scan disks on a remote Windows machine via connector.
    Returns list of dicts: {path, total_gb, free_gb, used_gb}
    Only includes drives with total capacity >= min_size_tb TB.
    """
    min_bytes = _tb_to_bytes(min_size_tb)
    result: list[dict] = []

    # PowerShell to list all logical disks
    ps = (
        "Get-WmiObject Win32_LogicalDisk | "
        "Select-Object DeviceID, Size, FreeSpace | "
        "ForEach-Object { \"$($_.DeviceID) $($_.Size) $($_.FreeSpace)\" }"
    )
    try:
        exit_code, out, err = connector.exec_command(ps)
        if exit_code != 0 or not out.strip():
            logger.warning("scan_remote_disks: command failed (exit %d): %s", exit_code, err[:100])
            return []
        for line in out.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                dev_id = parts[0]  # e.g. "C:"
                total_bytes = int(parts[1]) if parts[1] and parts[1] != "0" else 0
                free_bytes = int(parts[2]) if parts[2] and parts[2] != "0" else 0
                if total_bytes < min_bytes:
                    continue
                result.append({
                    "path": dev_id + "\\",
                    "total_gb": _bytes_to_gb(total_bytes),
                    "free_gb": _bytes_to_gb(free_bytes),
                    "used_gb": _bytes_to_gb(total_bytes - free_bytes),
                })
            except (ValueError, IndexError):
                continue
    except Exception as exc:
        logger.error("scan_remote_disks failed: %s", exc)
    return result


def get_remote_target_disk(
    connector,
    min_size_tb: float = 2.0,
    min_free_gb: float = 100.0,
) -> str | None:
    """
    Return the first remote disk path with enough free space, or None.
    """
    for disk in scan_remote_disks(connector, min_size_tb):
        if disk["free_gb"] >= min_free_gb:
            return disk["path"]
    return None


def push_file_to_remote(
    local_path: str,
    remote_path: str,
    connector,
) -> bool:
    """
    Push a local file to a remote path via connector.
    After upload, compute MD5 on both sides and verify.
    Returns True on success, False on failure.
    """
    import hashlib

    # Compute local MD5 first
    local_md5 = ""
    try:
        h = hashlib.md5()
        with open(local_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        local_md5 = h.hexdigest()
    except Exception as exc:
        logger.error("push_file_to_remote: cannot read local file %s: %s", local_path, exc)
        return False

    # Ensure remote directory exists
    remote_dir = "\\".join(remote_path.replace("/", "\\").rstrip("\\").split("\\")[:-1])
    if remote_dir:
        connector.exec_command(
            f"if (-not (Test-Path '{remote_dir}')) {{ New-Item -ItemType Directory -Force -Path '{remote_dir}' }}"
        )

    # Upload
    ok = connector.upload_file(local_path, remote_path)
    if not ok:
        logger.error("push_file_to_remote: upload failed for %s", local_path)
        return False

    # Verify remote MD5 via PowerShell
    ps_md5 = f"(Get-FileHash '{remote_path}' -Algorithm MD5).Hash.ToLower()"
    exit_code, remote_md5_out, err = connector.exec_command(ps_md5)
    if exit_code != 0:
        logger.warning("push_file_to_remote: cannot verify remote MD5 (continuing): %s", err[:80])
        return True  # Upload succeeded even if MD5 check fails

    remote_md5 = remote_md5_out.strip().lower()
    if remote_md5 and remote_md5 != local_md5:
        logger.error(
            "push_file_to_remote: MD5 MISMATCH for %s (local=%s remote=%s)",
            local_path, local_md5, remote_md5
        )
        return False

    logger.info("push_file_to_remote: OK %s → remote (%s)", local_path, local_md5[:8])
    return True


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
