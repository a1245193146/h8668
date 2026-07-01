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
import shlex
import time
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


def _find_latest_full_on_storage(config: dict[str, Any], name_glob: str) -> dict[str, str] | None:
    """Find the newest full backup on the storage server matching name_glob."""
    connector: Any | None = None
    try:
        _ensure_src_on_path()
        try:
            import utils as _utils
        except ImportError as exc:
            logger.warning("_find_latest_full_on_storage import failed: %s", exc)
            return None

        connector = _utils.create_target_connector(config)
        if connector is None:
            return None

        base_path = str(config.get("backup_target", {}).get("base_path", "E:\\Backups"))
        base_literal = _ps_single_quote(base_path)
        glob_literal = _ps_single_quote(name_glob)
        ps = (
            f"$base={base_literal}; if (Test-Path $base) {{ "
            "Get-ChildItem -Path $base -Recurse -File | "
            f"Where-Object {{ $_.Name -like {glob_literal} }} | "
            "Sort-Object LastWriteTimeUtc -Descending | "
            "Select-Object -First 1 | "
            "ForEach-Object { Write-Output (\"{0}|{1}|{2}\" -f "
            "$_.FullName, $_.Directory.Name, $_.Name) } }"
        )
        code, out, err = connector.exec_command(ps)
        if code != 0:
            logger.warning("_find_latest_full_on_storage failed: %s", (err or out)[:200])
            return None

        line = next((item.strip() for item in out.splitlines() if item.strip()), "")
        if not line:
            return None
        parts = line.split("|", 2)
        if len(parts) != 3 or not all(parts):
            logger.warning("_find_latest_full_on_storage malformed output: %s", line[:200])
            return None
        remote_path, job, filename = parts
        return {"remote_path": remote_path, "job": job, "filename": filename}
    except Exception as exc:
        logger.warning("_find_latest_full_on_storage failed: %s", exc)
        return None
    finally:
        if connector is not None:
            try:
                connector.close()
            except Exception:
                logger.debug("Ignoring storage connector close failure", exc_info=True)


def _find_latest_mysql_backup(config: dict[str, Any]) -> dict[str, str] | None:
    """Find the most recent MySQL full backup on the storage server."""
    return _find_latest_full_on_storage(config, "*_full.sql.gz")


def _find_latest_sqlserver_backup(config: dict[str, Any]) -> dict[str, str] | None:
    """Find the most recent SQL Server full backup on the storage server."""
    return _find_latest_full_on_storage(config, "*_full.bak")


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
    Restore the latest MySQL backup from storage to the Linux test machine and verify.

    Steps:
    1. Find latest MySQL full backup file on the storage server
    2. SSH-connect to linux_machine test server
    3. Pull the backup directly from storage with smbclient
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
        backup = _find_latest_mysql_backup(config)

        if not backup:
            return _make_result(
                success=False,
                db_name="mysql",
                started_at=started_at,
                error_msg="No MySQL full backup found on storage",
            )

        target = config.get("backup_target", {})
        try:
            base_path = str(target.get("base_path", "E:\\Backups"))
            base_sub = base_path.split(":", 1)[1].lstrip("\\")
            drive_letter = base_path.split(":", 1)[0]
            admin_share = f"{drive_letter}$"
            storage_ip = str(target["host"])
            store_domain = str(target.get("domain", ""))
            store_user = str(target.get("username", ""))
            store_pw = str(target.get("password", ""))
        except (KeyError, IndexError) as exc:
            return _make_result(
                success=False,
                db_name="mysql",
                started_at=started_at,
                error_msg=f"backup_target storage config invalid: {exc}",
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

        job = backup["job"]
        filename = backup["filename"]
        smb_user = f"{store_domain}\\{store_user}" if store_domain else store_user
        remote_tmp = f"/tmp/restore_test_{filename}"
        smb_script = f'prompt OFF; cd "{base_sub}\\{job}"; get "{filename}" {remote_tmp}'
        smb_cmd = (
            f"smbclient //{storage_ip}/{admin_share} -U {shlex.quote(smb_user + '%' + store_pw)} "
            f"-c {shlex.quote(smb_script)} 2>&1; echo SMB_DONE"
        )
        logger.info("verify_mysql_restore: pulling %s → %s", backup["remote_path"], remote_tmp)
        _code, smb_out, _err = conn.exec_command(smb_cmd)
        if "NT_STATUS" in (smb_out or ""):
            bad_lines = [line for line in smb_out.splitlines() if "NT_STATUS" in line and "COLLISION" not in line]
            if bad_lines:
                return _make_result(
                    success=False,
                    db_name="mysql",
                    started_at=started_at,
                    error_msg=f"smbclient pull failed: {bad_lines[0][:200]}",
                )

        exit_code, got_out, err = conn.exec_command(f"test -f {shlex.quote(remote_tmp)} && echo GOT")
        if exit_code != 0 or "GOT" not in got_out:
            return _make_result(
                success=False,
                db_name="mysql",
                started_at=started_at,
                error_msg=f"Backup pull failed; file not found on test machine: {(smb_out or err)[:200]}",
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


def _parse_sqlserver_filelist(output: str) -> list[tuple[str, str]]:
    """Parse sqlcmd RESTORE FILELISTONLY rows into (logical_name, file_type)."""
    files: list[tuple[str, str]] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) >= 3 and parts[0] and parts[2] in {"D", "L"}:
            files.append((parts[0], parts[2]))
    return files


def _first_sqlcmd_value(output: str) -> str:
    """Return the first useful scalar value from sqlcmd output."""
    for line in output.splitlines():
        value = line.strip()
        if not value or value.startswith("-") or value.startswith("("):
            continue
        return value
    return ""


def _safe_restore_file_stem(value: str) -> str:
    """Make a SQL Server logical file name safe for a restore target filename."""
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value)
    return safe.strip("_") or "file"


def verify_sqlserver_restore(config: dict[str, Any]) -> RestoreResult:
    """
    Restore the latest SQL Server backup from storage to the Windows test machine and verify.

    Steps:
    1. Find latest SQL Server full backup (.bak) on the storage server
    2. Connect to windows_machine via WindowsConnector (WinRM/SSH)
    3. Pull the .bak directly from storage over SMB
    4. RESTORE DATABASE with REPLACE and MOVE when file metadata is available
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
        backup = _find_latest_sqlserver_backup(config)

        if not backup:
            return _make_result(
                success=False,
                db_name="sqlserver",
                started_at=started_at,
                error_msg="No SQL Server full backup found on storage",
            )

        target = config.get("backup_target", {})
        try:
            base_path = str(target.get("base_path", "E:\\Backups"))
            base_sub = base_path.split(":", 1)[1].lstrip("\\")
            drive_letter = base_path.split(":", 1)[0]
            admin_share = f"{drive_letter}$"
            storage_ip = str(target["host"])
            store_domain = str(target.get("domain", ""))
            store_user = str(target.get("username", ""))
            store_pw = str(target.get("password", ""))
        except (KeyError, IndexError) as exc:
            return _make_result(
                success=False,
                db_name="sqlserver",
                started_at=started_at,
                error_msg=f"backup_target storage config invalid: {exc}",
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

        job = backup["job"]
        filename = backup["filename"]
        share_path = f"\\\\{storage_ip}\\{admin_share}"
        unc_source = f"{share_path}\\{base_sub}\\{job}\\{filename}"
        remote_bak = f"C:\\Temp\\restore_test_{filename}"
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

        full_user = f"{store_domain}\\{store_user}" if store_domain else store_user
        ps_copy = (
            f"net use {share_path} {_ps_single_quote(store_pw)} /user:{full_user} | Out-Null; "
            f"Copy-Item -Path {_ps_single_quote(unc_source)} -Destination {_ps_single_quote(remote_bak)} -Force; "
            f"$ok = Test-Path {_ps_single_quote(remote_bak)}; "
            f"net use {share_path} /delete /y | Out-Null; "
            "if ($ok) { Write-Output 'COPY_OK' } else { Write-Output 'COPY_FAIL' }"
        )
        logger.info("verify_sqlserver_restore: pulling %s → %s", backup["remote_path"], remote_bak)
        code, copy_out, err = conn.exec_command(ps_copy)
        if code != 0 or "COPY_OK" not in copy_out:
            return _make_result(
                success=False,
                db_name="sqlserver",
                started_at=started_at,
                error_msg=f"SMB copy failed: {(err or copy_out)[:300]}",
            )

        filelist_sql = f"RESTORE FILELISTONLY FROM DISK=N{_sql_single_quote(remote_bak)}"
        filelist_cmd = f"sqlcmd -S {_ps_single_quote(sql_instance)} -Q {_ps_single_quote(filelist_sql)} -h-1 -W -s \"|\""
        _code, filelist_out, _err = conn.exec_command(filelist_cmd)
        filelist = _parse_sqlserver_filelist(filelist_out)

        default_dir_sql = "SET NOCOUNT ON; SELECT CONVERT(nvarchar(4000), SERVERPROPERTY('InstanceDefaultDataPath'))"
        default_dir_cmd = f"sqlcmd -S {_ps_single_quote(sql_instance)} -Q {_ps_single_quote(default_dir_sql)} -h-1 -W"
        _code, default_dir_out, _err = conn.exec_command(default_dir_cmd)
        default_data_dir = _first_sqlcmd_value(default_dir_out)
        if not default_data_dir or default_data_dir.upper() == "NULL":
            default_data_dir = "C:\\Temp\\"
        if not default_data_dir.endswith(("\\", "/")):
            default_data_dir += "\\"

        move_clauses: list[str] = []
        data_index = 0
        log_index = 0
        for logical_name, file_type in filelist:
            if file_type == "L":
                log_index += 1
                index = log_index
                extension = "ldf"
            else:
                data_index += 1
                index = data_index
                extension = "mdf"
            safe_logical = _safe_restore_file_stem(logical_name)
            target_file = f"{default_data_dir}{test_db}_{safe_logical}_{index}.{extension}"
            move_clauses.append(
                f", MOVE N{_sql_single_quote(logical_name)} TO N{_sql_single_quote(target_file)}"
            )

        restore_sql = (
            f"RESTORE DATABASE [{test_db}] "
            f"FROM DISK=N{_sql_single_quote(remote_bak)} "
            f"WITH REPLACE, RECOVERY{''.join(move_clauses)}"
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


def run_monthly_verification(config: dict[str, Any], force: bool = False) -> list[RestoreResult]:
    """
    Run both MySQL and SQL Server restore verifications on the scheduled day.

    Returns list of RestoreResult dicts. When force is False and restore testing is
    disabled or today is not config['restore_test']['schedule_day'], returns an empty list.
    When force is True, skips the schedule-day gate and runs immediately.
    Sends alerts via alerter for each result.
    """
    try:
        if not force and not is_verification_day(config):
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
