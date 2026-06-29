from __future__ import annotations

# ---- MD5 VALIDATION ----

import hashlib
import datetime
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CHUNK_SIZE = 8192  # 8KB streaming reads


def compute_md5_stream(file_path: str) -> str:
    """
    Compute MD5 of a file using 8KB streaming reads.
    Never loads entire file into memory.
    Returns hex digest string (32 chars).
    Raises FileNotFoundError if file missing.
    """
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_md5(file_path: str, md5_sidecar_path: str) -> bool:
    """
    Verify file_path against its .md5 sidecar file.
    Returns True if hashes match, False otherwise.
    Logs mismatch details.
    """
    try:
        with open(md5_sidecar_path, "r") as f:
            expected = f.read().strip().split()[0]  # handle "hash  filename" format
    except FileNotFoundError:
        logger.error("MD5 sidecar not found: %s", md5_sidecar_path)
        return False

    try:
        actual = compute_md5_stream(file_path)
    except FileNotFoundError:
        logger.error("Backup file not found: %s", file_path)
        return False

    if actual != expected:
        logger.error(
            "MD5 MISMATCH for %s: expected=%s actual=%s",
            file_path,
            expected,
            actual,
        )
        return False

    logger.info("MD5 verified OK: %s", file_path)
    return True


def validate_backup_file(backup_result: dict) -> dict:
    """
    Full validation of a backup result dict.
    Checks:
    1. file_path is not None
    2. File exists on disk
    3. File size > 0
    4. MD5 sidecar exists (file_path + '.md5')
    5. MD5 hash matches

    Returns ValidationResult dict:
    {
        "valid": bool,
        "file_path": str | None,
        "expected_md5": str | None,
        "actual_md5": str | None,
        "error": str | None,
    }
    """
    file_path: str | None = backup_result.get("file_path")

    result: dict = {
        "valid": False,
        "file_path": file_path,
        "expected_md5": None,
        "actual_md5": None,
        "error": None,
    }

    # 1. file_path must not be None
    if file_path is None:
        result["error"] = "file_path is None in backup_result"
        logger.error("validate_backup_file: %s", result["error"])
        return result

    # 2. File must exist on disk
    if not os.path.exists(file_path):
        result["error"] = f"Backup file not found on disk: {file_path}"
        logger.error("validate_backup_file: %s", result["error"])
        return result

    # 3. File size must be > 0
    if os.path.getsize(file_path) == 0:
        result["error"] = f"Backup file is empty (0 bytes): {file_path}"
        logger.error("validate_backup_file: %s", result["error"])
        return result

    # 4. MD5 sidecar must exist
    md5_path = file_path + ".md5"
    if not os.path.exists(md5_path):
        result["error"] = f"MD5 sidecar not found: {md5_path}"
        logger.error("validate_backup_file: %s", result["error"])
        return result

    # Read expected hash from sidecar
    try:
        with open(md5_path, "r") as f:
            expected_md5 = f.read().strip().split()[0]
    except OSError as exc:
        result["error"] = f"Cannot read MD5 sidecar {md5_path}: {exc}"
        logger.error("validate_backup_file: %s", result["error"])
        return result

    result["expected_md5"] = expected_md5

    # 5. Hash must match
    try:
        actual_md5 = compute_md5_stream(file_path)
    except OSError as exc:
        result["error"] = f"Cannot read backup file for hashing {file_path}: {exc}"
        logger.error("validate_backup_file: %s", result["error"])
        return result

    result["actual_md5"] = actual_md5

    if actual_md5 != expected_md5:
        result["error"] = (
            f"MD5 mismatch: expected={expected_md5} actual={actual_md5}"
        )
        logger.error("validate_backup_file: %s for %s", result["error"], file_path)
        return result

    result["valid"] = True
    logger.info("validate_backup_file: OK for %s (md5=%s)", file_path, actual_md5)
    return result


# ---- CHAIN AUDIT (added by Task 10) ----

# Per-type file extension mapping
_BACKUP_EXTENSIONS = {
    "sqlserver": ("bak", {"full": "full", "incr": "diff"}),
    "mysql":     ("sql.gz", {"full": "full", "incr": "incr"}),
    "sqlite":    ("db.gz", {"full": "full", "incr": "incr"}),
    "file":      ("zip", {"full": "full", "incr": "incr"}),
}


def get_chain_files(
    job_name: str,
    backup_dir: str,
    backup_type: str,
) -> tuple[list[str], list[str]]:
    """
    Scan backup_dir for non-empty full backup files for a given job.

    Returns (found_files, missing_dates):
    - found_files: list of absolute full backup paths, ordered by date
    - missing_dates: ["full"] when no valid full exists
    """
    ext, suffixes = _BACKUP_EXTENSIONS.get(backup_type, ("bak", {"full": "full", "incr": "incr"}))
    backup_path = Path(backup_dir)
    found = sorted(
        str(path)
        for path in backup_path.glob(f"{job_name}_*_{suffixes['full']}.{ext}")
        if path.is_file() and path.stat().st_size > 0
    )
    missing = [] if found else ["full"]

    return found, missing


def audit_backup_chain(job_config: dict, backup_dir: str) -> dict:
    """
    Audit whether the rolling backup chain is intact for a given job.

    Returns ChainAuditResult:
    {
        "intact": bool,
        "missing_files": list[str],   # YYYYMMDD strings
        "last_valid_date": str | None,
        "recommendation": str,        # "proceed_incremental" | "force_full"
    }
    """
    job_name = job_config.get("name", "unknown")
    backup_type = job_config.get("type", "sqlserver")
    history = job_config.get("history") or {}

    found, missing = get_chain_files(job_name, backup_dir, backup_type)

    intact = len(missing) == 0
    last_valid = None
    if found:
        # Extract date from last found filename
        last_fname = Path(found[-1]).name
        parts = last_fname.split("_")
        if len(parts) >= 2:
            last_valid = parts[1]  # YYYYMMDD portion

    try:
        increments_since_full = int(history.get("increments_since_full", 0))
    except (ValueError, TypeError):
        increments_since_full = 6
    force_full = not intact or increments_since_full >= 6
    recommendation = "force_full" if force_full else "proceed_incremental"

    result = {
        "intact": intact,
        "missing_files": missing,
        "last_valid_date": last_valid,
        "recommendation": recommendation,
    }
    logger.info(
        "audit_backup_chain: job=%r intact=%s missing=%s",
        job_name, intact, missing
    )
    return result


def fuse_check(audit_result: dict) -> str:
    """
    Given a ChainAuditResult, return the backup mode recommendation.
    Returns "force_full" or "proceed_incremental".
    """
    if audit_result.get("recommendation") == "force_full":
        return "force_full"
    if not audit_result.get("intact", False):
        return "force_full"
    return "proceed_incremental"


def audit_backup_chain_remote(job_config: dict, connector, base_path: str) -> dict:
    """Audit the SQL Server backup chain ON THE STORAGE SERVER.

    Server-to-server SQL Server backups live on the storage host
    (e.g. E:\\Backups\\<job>\\<job>_YYYYMMDD_full.bak), not on the orchestrator.
    This lists the storage directory over the connector and applies the same
    rolling full+diff continuity logic as audit_backup_chain.
    Returns the same ChainAuditResult dict. On any remote error it returns a
    permissive intact=True result so a transient storage hiccup does NOT force
    a needless full (the scheduler's last_full age rule still applies).
    """
    job_name = job_config.get("name", "unknown")
    history = job_config.get("history") or {}
    # storage layout: <base_path>\<job_name>\  (base_path like "E:\Backups")
    remote_dir = f"{str(base_path).rstrip(chr(92))}\\{job_name}"
    ps = (
        f"if (Test-Path '{remote_dir}') {{ "
        f"Get-ChildItem -Path '{remote_dir}' -Filter '*.bak' -File | "
        f"ForEach-Object {{ if ($_.Length -gt 0) {{ Write-Output $_.Name }} }} }}"
    )
    try:
        code, out, _err = connector.exec_command(ps)
        if code != 0:
            logger.warning("audit_backup_chain_remote: list failed for %s (code %s)", job_name, code)
            return {"intact": True, "missing_files": [], "last_valid_date": None,
                    "recommendation": "proceed_incremental"}
        present = {ln.strip() for ln in out.splitlines() if ln.strip()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("audit_backup_chain_remote error for %s: %s", job_name, exc)
        return {"intact": True, "missing_files": [], "last_valid_date": None,
                "recommendation": "proceed_incremental"}

    full_names = sorted(name for name in present if name.startswith(f"{job_name}_") and name.endswith("_full.bak"))
    intact = bool(full_names)
    latest_full = full_names[-1].split("_")[-2] if full_names else None
    try:
        increments_since_full = int(history.get("increments_since_full", 0))
    except (ValueError, TypeError):
        increments_since_full = 6
    force_full = not intact or increments_since_full >= 6
    return {
        "intact": intact,
        "missing_files": [] if intact else ["full"],
        "last_valid_date": latest_full,
        "recommendation": "force_full" if force_full else "proceed_incremental",
    }
