"""restore_verifier.py — Monthly automatic restore verification.

Verifies backup recoverability by:
1. Finding the most recent full backup for each DB type
2. Restoring it to a designated test machine
3. Querying to confirm data is accessible
4. Reporting success/failure via alerter

Runs monthly on config['restore_test']['schedule_day'] (default: 1st).
"""

from __future__ import annotations

import datetime
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RestoreResult = dict[str, Any]  # {success, db_name, tables_count, duration_seconds, error_msg, tested_at}


def _fallback_send_alert(level: str, msg: str, cfg: dict[str, Any], job_name: str = "") -> None:
    """Log alerts when alerter.py is unavailable."""
    del cfg, job_name
    logger.log(40 if level == "error" else 20, "[%s] %s", level.upper(), msg)


def _make_result(
    *,
    success: bool,
    db_name: str,
    started_at: float,
    tables_count: int = 0,
    error_msg: str | None = None,
) -> RestoreResult:
    return {
        "success": success,
        "db_name": db_name,
        "tables_count": tables_count,
        "duration_seconds": round(time.time() - started_at, 2),
        "error_msg": error_msg,
        "tested_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def _find_latest_backup(backup_dir: str, pattern: str) -> str | None:
    """Find the most recent file in backup_dir matching pattern. Returns None if not found."""
    try:
        candidates = sorted(
            Path(backup_dir).glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return str(candidates[0]) if candidates else None
    except Exception as exc:
        logger.warning("_find_latest_backup failed for %s: %s", backup_dir, exc)
        return None


def _find_latest_mysql_backup(config: dict[str, Any]) -> str | None:
    """Find the most recent MySQL full backup across all configured mysql jobs."""
    try:
        for job in config.get("databases", []):
            if job.get("type") == "mysql" and job.get("enabled", False):
                backup_dir = job.get("backup_dir", "")
                if backup_dir:
                    backup_file = _find_latest_backup(backup_dir, "*_full.sql.gz")
                    if backup_file:
                        return backup_file
    except Exception as exc:
        logger.warning("_find_latest_mysql_backup failed: %s", exc)
    return None


def _find_latest_sqlserver_backup(config: dict[str, Any]) -> str | None:
    """Find the most recent SQL Server full backup."""
    try:
        for job in config.get("databases", []):
            if job.get("type") == "sqlserver" and job.get("enabled", False):
                backup_dir = job.get("backup_dir", "")
                if backup_dir:
                    backup_file = _find_latest_backup(backup_dir, "*_full.bak")
                    if backup_file:
                        return backup_file
    except Exception as exc:
        logger.warning("_find_latest_sqlserver_backup failed: %s", exc)
    return None


def _ensure_src_on_path() -> None:
    """Allow `python -c` imports from project root with sys.path.insert(0, 'src')."""
    import sys as _sys

    src_dir = os.path.dirname(os.path.abspath(__file__))
    if src_dir not in _sys.path:
        _sys.path.insert(0, src_dir)


def _sh_single_quote(value: str) -> str:
    """Quote a value as a POSIX shell single-quoted literal."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def verify_mysql_restore(config: dict[str, Any]) -> RestoreResult:
    """
    Restore the latest MySQL backup to the Linux test machine and verify.

    Steps:
    1. Find latest MySQL full backup file on this (scheduling) machine
    2. SSH-connect to linux_machine test server
    3. Upload the backup file
    4. DROP and recreate test_db
    5. Restore with gunzip | mysql
    6. Query table count to verify
    """
    started_at = time.time()
    conn: Any | None = None

    try:
        rt = config.get("restore_test", {})
        lm = rt.get("linux_machine", {})

        if not lm:
            return _make_result(
                success=False,
                db_name="mysql",
                started_at=started_at,
                error_msg="restore_test.linux_machine not configured",
            )

        required_keys = ("host", "username", "password")
        missing = [key for key in required_keys if not lm.get(key)]
        if missing:
            return _make_result(
                success=False,
                db_name="mysql",
                started_at=started_at,
                error_msg=f"restore_test.linux_machine missing required keys: {', '.join(missing)}",
            )

        test_db = str(lm.get("test_db", "restore_test_db"))
        mysql_user = str(lm.get("mysql_user", "root"))
        mysql_pass = str(lm.get("mysql_password", ""))
        backup_file = _find_latest_mysql_backup(config)

        if not backup_file:
            return _make_result(
                success=False,
                db_name="mysql",
                started_at=started_at,
                error_msg="No MySQL full backup found locally",
            )

        _ensure_src_on_path()
        try:
            from connector import SSHPasswordConnector
        except ImportError as exc:
            return _make_result(
                success=False,
                db_name="mysql",
                started_at=started_at,
                error_msg=f"connector import failed: {exc}",
            )

        conn = SSHPasswordConnector(
            host=str(lm["host"]),
            username=str(lm["username"]),
            password=str(lm["password"]),
            port=int(lm.get("port", 22)),
            max_retries=2,
            retry_delay=10,
        )

        remote_tmp = f"/tmp/restore_test_{os.path.basename(backup_file)}"
        logger.info("verify_mysql_restore: uploading %s → %s", backup_file, remote_tmp)
        if not conn.upload_file(backup_file, remote_tmp):
            return _make_result(
                success=False,
                db_name="mysql",
                started_at=started_at,
                error_msg="Failed to upload backup file to test machine",
            )

        mysql_auth = f"-u{_sh_single_quote(mysql_user)} -p{_sh_single_quote(mysql_pass)}"
        drop_create = (
            f"mysql {mysql_auth} -e "
            f"{_sh_single_quote(f'DROP DATABASE IF EXISTS `{test_db}`; CREATE DATABASE `{test_db}`;')}"
        )
        exit_code, _, err = conn.exec_command(drop_create)
        if exit_code != 0:
            return _make_result(
                success=False,
                db_name="mysql",
                started_at=started_at,
                error_msg=f"DROP/CREATE failed: {err[:200]}",
            )

        restore_cmd = f"gunzip < {_sh_single_quote(remote_tmp)} | mysql {mysql_auth} {_sh_single_quote(test_db)}"
        exit_code, _, err = conn.exec_command(restore_cmd)
        if exit_code != 0:
            return _make_result(
                success=False,
                db_name="mysql",
                started_at=started_at,
                error_msg=f"Restore failed: {err[:200]}",
            )

        verify_sql = (
            "SELECT COUNT(*) FROM information_schema.tables "
            f"WHERE table_schema={_sql_single_quote(test_db)}"
        )
        verify_cmd = f"mysql {mysql_auth} -N -e {_sh_single_quote(verify_sql)}"
        exit_code, out, err = conn.exec_command(verify_cmd)
        if exit_code != 0:
            return _make_result(
                success=False,
                db_name="mysql",
                started_at=started_at,
                error_msg=f"Verification query failed: {err[:200]}",
            )

        tables_count = int(out.strip()) if out.strip().isdigit() else 0
        conn.exec_command(f"rm -f {_sh_single_quote(remote_tmp)}")

        logger.info(
            "verify_mysql_restore: SUCCESS — db=%s tables=%d duration=%.1fs",
            test_db,
            tables_count,
            time.time() - started_at,
        )
        return _make_result(
            success=True,
            db_name="mysql",
            started_at=started_at,
            tables_count=tables_count,
        )
    except Exception as exc:
        return _make_result(
            success=False,
            db_name="mysql",
            started_at=started_at,
            error_msg=f"Unexpected error: {exc}",
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.debug("Ignoring MySQL restore connector close failure", exc_info=True)


def _sql_single_quote(value: str) -> str:
    """Quote a string for SQL single-quoted literals."""
    return "'" + value.replace("'", "''") + "'"


def _ps_single_quote(value: str) -> str:
    """Quote a string for PowerShell single-quoted literals."""
    return "'" + value.replace("'", "''") + "'"


def verify_sqlserver_restore(config: dict[str, Any]) -> RestoreResult:
    """
    Restore the latest SQL Server backup to the Windows test machine and verify.

    Steps:
    1. Find latest SQL Server full backup (.bak)
    2. Connect to windows_machine via WindowsConnector (WinRM/SSH)
    3. Upload the .bak file to test machine
    4. RESTORE DATABASE with REPLACE
    5. Query sys.tables count
    """
    started_at = time.time()
    conn: Any | None = None

    try:
        rt = config.get("restore_test", {})
        wm = rt.get("windows_machine", {})

        if not wm:
            return _make_result(
                success=False,
                db_name="sqlserver",
                started_at=started_at,
                error_msg="restore_test.windows_machine not configured",
            )

        required_keys = ("host", "username", "password")
        missing = [key for key in required_keys if not wm.get(key)]
        if missing:
            return _make_result(
                success=False,
                db_name="sqlserver",
                started_at=started_at,
                error_msg=f"restore_test.windows_machine missing required keys: {', '.join(missing)}",
            )

        test_db = str(wm.get("test_db", "restore_test_db"))
        sql_instance = str(wm.get("sqlserver_instance", "localhost"))
        backup_file = _find_latest_sqlserver_backup(config)

        if not backup_file:
            return _make_result(
                success=False,
                db_name="sqlserver",
                started_at=started_at,
                error_msg="No SQL Server full backup found locally",
            )

        _ensure_src_on_path()
        try:
            from connector import WindowsConnector
        except ImportError as exc:
            return _make_result(
                success=False,
                db_name="sqlserver",
                started_at=started_at,
                error_msg=f"connector import failed: {exc}",
            )

        conn = WindowsConnector(
            host=str(wm["host"]),
            username=str(wm["username"]),
            password=str(wm["password"]),
            domain=str(wm.get("domain", "")),
            winrm_port=int(wm.get("winrm_port", 5985)),
            ssh_port=int(wm.get("ssh_port", 22)),
        )

        remote_bak = f"C:\\Temp\\restore_test_{os.path.basename(backup_file)}"
        code, _, err = conn.exec_command(
            "if (-not (Test-Path C:\\Temp)) { New-Item -ItemType Directory C:\\Temp | Out-Null }"
        )
        if code != 0:
            return _make_result(
                success=False,
                db_name="sqlserver",
                started_at=started_at,
                error_msg=f"Failed to prepare remote temp directory: {err[:200]}",
            )

        logger.info("verify_sqlserver_restore: uploading %s → %s", backup_file, remote_bak)
        if not conn.upload_file(backup_file, remote_bak):
            return _make_result(
                success=False,
                db_name="sqlserver",
                started_at=started_at,
                error_msg="Failed to upload .bak to test machine",
            )

        restore_sql = (
            f"RESTORE DATABASE [{test_db}] "
            f"FROM DISK=N{_sql_single_quote(remote_bak)} "
            "WITH REPLACE, RECOVERY"
        )
        restore_cmd = f"sqlcmd -S {_ps_single_quote(sql_instance)} -Q {_ps_single_quote(restore_sql)}"
        exit_code, _, err = conn.exec_command(restore_cmd)
        if exit_code != 0:
            return _make_result(
                success=False,
                db_name="sqlserver",
                started_at=started_at,
                error_msg=f"RESTORE failed: {err[:200]}",
            )

        verify_sql = f"SELECT COUNT(*) FROM [{test_db}].sys.tables"
        verify_cmd = f"sqlcmd -S {_ps_single_quote(sql_instance)} -Q {_ps_single_quote(verify_sql)} -h-1"
        exit_code, out, err = conn.exec_command(verify_cmd)
        if exit_code != 0:
            return _make_result(
                success=False,
                db_name="sqlserver",
                started_at=started_at,
                error_msg=f"Verify query failed: {err[:200]}",
            )

        tables_count = 0
        for line in out.strip().splitlines():
            stripped = line.strip()
            if stripped.isdigit():
                tables_count = int(stripped)
                break

        conn.exec_command(f"Remove-Item -Force {_ps_single_quote(remote_bak)} -ErrorAction SilentlyContinue")

        logger.info(
            "verify_sqlserver_restore: SUCCESS — db=%s tables=%d duration=%.1fs",
            test_db,
            tables_count,
            time.time() - started_at,
        )
        return _make_result(
            success=True,
            db_name="sqlserver",
            started_at=started_at,
            tables_count=tables_count,
        )
    except Exception as exc:
        return _make_result(
            success=False,
            db_name="sqlserver",
            started_at=started_at,
            error_msg=f"Unexpected error: {exc}",
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.debug("Ignoring SQL Server restore connector close failure", exc_info=True)


def is_verification_day(config: dict[str, Any]) -> bool:
    """Return True if today is the scheduled monthly verification day (UTC)."""
    try:
        rt = config.get("restore_test", {})
        if not rt.get("enabled", False):
            return False
        schedule_day = int(rt.get("schedule_day", 1))
        today = datetime.datetime.now(datetime.timezone.utc)
        return today.day == schedule_day
    except Exception as exc:
        logger.warning("is_verification_day failed: %s", exc)
        return False


def run_monthly_verification(config: dict[str, Any]) -> list[RestoreResult]:
    """
    Run both MySQL and SQL Server restore verifications on the scheduled day.

    Returns list of RestoreResult dicts. When restore testing is disabled or today is
    not config['restore_test']['schedule_day'], returns an empty list.
    Sends alerts via alerter for each result.
    """
    try:
        if not is_verification_day(config):
            logger.info("run_monthly_verification: skipped; not scheduled verification day")
            return []

        _ensure_src_on_path()
        try:
            from alerter import send_alert as alert_sender
        except ImportError:
            alert_sender = _fallback_send_alert

        results: list[RestoreResult] = []
        logger.info("run_monthly_verification: starting restore verification")

        mysql_result = verify_mysql_restore(config)
        results.append(mysql_result)
        if mysql_result["success"]:
            alert_sender(
                "info",
                f"[RESTORE-VERIFY] MySQL restore OK — {mysql_result['tables_count']} tables verified "
                f"({mysql_result['duration_seconds']:.1f}s)",
                config,
                "restore_verifier",
            )
        else:
            alert_sender(
                "error",
                f"[RESTORE-VERIFY] MySQL restore FAILED: {mysql_result['error_msg']}",
                config,
                "restore_verifier",
            )

        sql_result = verify_sqlserver_restore(config)
        results.append(sql_result)
        if sql_result["success"]:
            alert_sender(
                "info",
                f"[RESTORE-VERIFY] SQL Server restore OK — {sql_result['tables_count']} tables verified "
                f"({sql_result['duration_seconds']:.1f}s)",
                config,
                "restore_verifier",
            )
        else:
            alert_sender(
                "error",
                f"[RESTORE-VERIFY] SQL Server restore FAILED: {sql_result['error_msg']}",
                config,
                "restore_verifier",
            )

        return results
    except Exception as exc:
        logger.error("run_monthly_verification failed: %s", exc)
        started_at = time.time()
        return [
            _make_result(
                success=False,
                db_name="restore_verifier",
                started_at=started_at,
                error_msg=f"Unexpected error: {exc}",
            )
        ]
