"""Backup executor implementations for SQL Server, MySQL, SQLite, and files."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

try:  # Supports both `python -m src.executor` and `sys.path.insert(0, 'src')` QA usage.
    from .utils import get_target_disk
except ImportError:  # pragma: no cover - exercised by the documented one-line QA commands.
    from utils import get_target_disk  # type: ignore[no-redef]

try:
    from .connector import SSHPasswordConnector
except ImportError:  # pragma: no cover - exercised by the documented one-line QA commands.
    from connector import SSHPasswordConnector  # type: ignore[no-redef]

BackupResult = dict[str, bool | str | float | None]


def _compute_md5_stream(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_md5_sidecar(file_path: str, md5_hex: str) -> None:
    with open(file_path + ".md5", "w", encoding="utf-8") as fh:
        fh.write(md5_hex + "\n")


def _result(
    *,
    success: bool,
    job_name: str,
    backup_type: str,
    started_at: float,
    file_path: str | None = None,
    md5: str | None = None,
    error_msg: str | None = None,
) -> BackupResult:
    size_mb = 0.0
    if file_path and os.path.exists(file_path):
        size_mb = round(os.path.getsize(file_path) / (1024 * 1024), 4)
    return {
        "success": success,
        "job_name": job_name,
        "backup_type": _public_backup_type(backup_type),
        "file_path": file_path if success else None,
        "file_size_mb": size_mb if success else 0.0,
        "md5": md5 if success else None,
        "duration_seconds": round(time.time() - started_at, 3),
        "error_msg": error_msg if not success else None,
    }


def _public_backup_type(backup_type: str) -> str:
    return "full" if backup_type == "full" else "incremental"


def _date_stamp() -> str:
    return datetime.now().strftime("%Y%m%d")


def _safe_job_name(job_config: dict[str, Any]) -> str:
    return str(job_config.get("name") or "backup")


def _ensure_out_dir(target_disk: str) -> str:
    out_dir = os.path.abspath(target_disk)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _cleanup_paths(*paths: str | None) -> None:
    for path in paths:
        if not path:
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _suffix_for(kind: str, backup_type: str) -> str:
    if backup_type == "full":
        return "full"
    return "diff" if kind == "sqlserver" else "incr"


def _backup_path(target_disk: str, job_name: str, kind: str, backup_type: str, extension: str) -> str:
    out_dir = _ensure_out_dir(target_disk)
    suffix = _suffix_for(kind, backup_type)
    return os.path.join(out_dir, f"{job_name}_{_date_stamp()}_{suffix}.{extension}")


def backup_sqlserver(
    job_config: dict[str, Any],
    target_disk: str,
    backup_type: str = "full",
    config: dict[str, Any] | None = None,
) -> BackupResult:
    """SQL Server backup.

    Uses server-to-server mode when the job describes the SQL Server machine
    (``host``) and a remote ``backup_target`` is configured; otherwise keeps
    the legacy local sqlcmd path for backward compatibility.
    """

    if job_config.get("host") and config and config.get("backup_target"):
        return _backup_sqlserver_remote(job_config, backup_type, config)
    return _backup_sqlserver_local(job_config, target_disk, backup_type)


def _backup_sqlserver_remote(
    job_config: dict[str, Any],
    backup_type: str,
    config: dict[str, Any],
) -> BackupResult:
    """Server-to-server SQL Server backup.

    BACKUP runs on the SQL Server host's own disk, then that host pushes the
    .bak directly to the storage server's admin share over SMB. The file never
    transits through the program machine.
    """

    import importlib

    started_at = time.time()
    job_name = _safe_job_name(job_config)

    try:
        connector_mod = importlib.import_module("connector")
        utils_mod = importlib.import_module("utils")
    except ImportError as exc:
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"import failed: {exc}",
        )

    target = config.get("backup_target", {})
    suffix = _suffix_for("sqlserver", backup_type)
    filename = f"{job_name}_{_date_stamp()}_{suffix}.bak"
    local_dir = str(job_config.get("local_backup_dir", "C:\\sqlbak")).rstrip("\\")
    local_bak = f"{local_dir}\\{filename}"
    sql_conn: Any | None = None
    store_conn: Any | None = None

    try:
        try:
            sql_conn = connector_mod.WindowsConnector(
                host=str(job_config["host"]),
                username=str(job_config.get("os_user", job_config.get("username", ""))),
                password=str(job_config.get("os_password", job_config.get("password", ""))),
                domain=str(job_config.get("domain", "")),
                winrm_port=int(job_config.get("winrm_port", 5985)),
                ssh_port=int(job_config.get("ssh_port", 22)),
            )
        except KeyError as err:
            return _result(
                success=False,
                job_name=job_name,
                backup_type=backup_type,
                started_at=started_at,
                error_msg=f"missing sqlserver machine config field: {err}",
            )

        store_conn = utils_mod.create_target_connector(config)
        if store_conn is None:
            return _result(
                success=False,
                job_name=job_name,
                backup_type=backup_type,
                started_at=started_at,
                error_msg="cannot create storage connector",
            )
        assert sql_conn is not None
        assert store_conn is not None

        try:
            sql_server = str(job_config.get("read_replica") or job_config.get("server", "localhost"))
            sa_user = str(job_config["auth"]["user"])
            sa_pw = str(job_config["auth"]["password"])
            db = str(job_config["database"])
        except KeyError as err:
            return _result(
                success=False,
                job_name=job_name,
                backup_type=backup_type,
                started_at=started_at,
                error_msg=f"missing sqlserver config field: {err}",
            )

        mode = "INIT, COMPRESSION" if backup_type == "full" else "DIFFERENTIAL, COMPRESSION"
        ps_backup = (
            f"if (-not (Test-Path {_ps_single_quote(local_dir)})) "
            f"{{ New-Item -ItemType Directory -Force {_ps_single_quote(local_dir)} | Out-Null }}; "
            f"sqlcmd -S {sql_server} -U {sa_user} -P {_ps_single_quote(sa_pw)} "
            f"-Q \"BACKUP DATABASE [{db}] TO DISK=N'{local_bak}' WITH {mode}\""
        )
        code, out, err = sql_conn.exec_command(ps_backup)
        if code != 0:
            return _result(
                success=False,
                job_name=job_name,
                backup_type=backup_type,
                started_at=started_at,
                error_msg=f"remote BACKUP failed: {(err or out)[:300]}",
            )

        code, src_md5_out, err = sql_conn.exec_command(
            f"(Get-FileHash -Algorithm MD5 {_ps_single_quote(local_bak)}).Hash.ToLower()"
        )
        src_md5 = src_md5_out.strip().splitlines()[-1].strip() if src_md5_out.strip() else ""
        if code != 0 or len(src_md5) != 32:
            return _result(
                success=False,
                job_name=job_name,
                backup_type=backup_type,
                started_at=started_at,
                error_msg=f"source MD5 failed: {(err or src_md5_out)[:200]}",
            )

        target_drive = utils_mod.get_remote_target_disk(
            store_conn,
            min_size_tb=float(target.get("min_size_tb", 2)),
            min_free_gb=float(target.get("min_free_gb", 100)),
        )
        if not target_drive:
            return _result(
                success=False,
                job_name=job_name,
                backup_type=backup_type,
                started_at=started_at,
                error_msg="no storage disk with >2TB total and >100GB free",
            )

        base_path = str(target.get("base_path", "E:\\Backups"))
        base_sub = base_path.split(":", 1)[1].lstrip("\\") if ":" in base_path else base_path.strip("\\")
        drive_letter = str(target_drive).rstrip("\\").rstrip(":")
        admin_share = f"{drive_letter}$"
        storage_ip = str(target["host"])
        store_domain = str(target.get("domain", ""))
        store_user = str(target.get("username", ""))
        store_pw = str(target.get("password", ""))
        full_user = f"{store_domain}\\{store_user}" if store_domain else store_user
        unc_dir = f"\\\\{storage_ip}\\{admin_share}\\{base_sub}\\{job_name}"
        unc_file = f"{unc_dir}\\{filename}"
        local_dest_for_hash = f"{drive_letter}:\\{base_sub}\\{job_name}\\{filename}"

        share_path = f"\\\\{storage_ip}\\{admin_share}"
        ps_copy = (
            f"net use {share_path} {_ps_single_quote(store_pw)} /user:{full_user} | Out-Null; "
            f"if (-not (Test-Path {_ps_single_quote(unc_dir)})) "
            f"{{ New-Item -ItemType Directory -Force {_ps_single_quote(unc_dir)} | Out-Null }}; "
            f"Copy-Item -Path {_ps_single_quote(local_bak)} -Destination {_ps_single_quote(unc_file)} -Force; "
            f"$ok = Test-Path {_ps_single_quote(unc_file)}; "
            f"net use {share_path} /delete /y | Out-Null; "
            "if ($ok) { Write-Output 'COPY_OK' } else { Write-Output 'COPY_FAIL' }"
        )
        code, copy_out, err = sql_conn.exec_command(ps_copy)
        if code != 0 or "COPY_OK" not in copy_out:
            return _result(
                success=False,
                job_name=job_name,
                backup_type=backup_type,
                started_at=started_at,
                error_msg=f"SMB copy failed: {(err or copy_out)[:300]}",
            )

        code, dst_md5_out, err = store_conn.exec_command(
            f"(Get-FileHash -Algorithm MD5 {_ps_single_quote(local_dest_for_hash)}).Hash.ToLower()"
        )
        dst_md5 = dst_md5_out.strip().splitlines()[-1].strip() if dst_md5_out.strip() else ""
        if code != 0 or dst_md5 != src_md5:
            return _result(
                success=False,
                job_name=job_name,
                backup_type=backup_type,
                started_at=started_at,
                error_msg=f"MD5 mismatch src={src_md5} dst={dst_md5}",
            )

        store_conn.exec_command(
            f"Set-Content -Path {_ps_single_quote(local_dest_for_hash + '.md5')} "
            f"-Value {_ps_single_quote(dst_md5)} -NoNewline"
        )
        sql_conn.exec_command(
            f"Remove-Item -Force {_ps_single_quote(local_bak)} -ErrorAction SilentlyContinue"
        )

        res = _result(
            success=True,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            file_path=local_dest_for_hash,
            md5=dst_md5,
        )
        res["on_storage"] = True
        return res
    except Exception as exc:  # noqa: BLE001 - executor must convert all failures into BackupResult.
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"unexpected: {exc}",
        )
    finally:
        if sql_conn is not None:
            try:
                sql_conn.close()
            except Exception:
                pass
        if store_conn is not None:
            try:
                store_conn.close()
            except Exception:
                pass


def _backup_sqlserver_local(
    job_config: dict[str, Any],
    target_disk: str,
    backup_type: str = "full",
) -> BackupResult:
    """Run SQL Server BACKUP DATABASE through sqlcmd and return a BackupResult."""

    started_at = time.time()
    job_name = _safe_job_name(job_config)
    out_path = _backup_path(target_disk, job_name, "sqlserver", backup_type, "bak")

    try:
        # U8/read-write separation: run BACKUP against the read replica when
        # configured to keep load-balanced primary workloads isolated.
        server = str(job_config.get("read_replica") or job_config["server"])
        db = str(job_config["database"])
        auth = job_config.get("auth") or {}
        user = str(auth["user"])
        password = str(auth["password"])
    except KeyError as err:
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"missing sqlserver config field: {err}",
        )

    def run_once(path: str) -> tuple[bool, str | None]:
        mode = "INIT, COMPRESSION" if backup_type == "full" else "DIFFERENTIAL, COMPRESSION"
        sql = f"BACKUP DATABASE [{db}] TO DISK=N'{path}' WITH {mode}"
        cmd = ["sqlcmd", "-S", server, "-U", user, "-P", password, "-Q", sql]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=7200,
            )
        except subprocess.TimeoutExpired:
            return False, "timeout running sqlcmd backup"
        except FileNotFoundError as err:
            return False, f"sqlcmd not found: {err}"

        if completed.returncode != 0:
            msg = (completed.stderr or completed.stdout or "sqlcmd backup failed").strip()
            return False, msg
        if not os.path.exists(path):
            return False, "sqlcmd returned success but backup file was not created"
        if os.path.getsize(path) <= 0:
            return False, "sqlcmd returned success but backup file is empty"
        return True, None

    ok, error = run_once(out_path)
    if not ok and error and "empty" in error.lower():
        _cleanup_paths(out_path, out_path + ".md5")
        retry_disk = get_target_disk()
        if retry_disk:
            out_path = _backup_path(retry_disk, job_name, "sqlserver", backup_type, "bak")
            ok, error = run_once(out_path)

    if not ok:
        _cleanup_paths(out_path, out_path + ".md5")
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=error or "sqlserver backup failed",
        )

    md5_hex = _compute_md5_stream(out_path)
    _write_md5_sidecar(out_path, md5_hex)
    return _result(
        success=True,
        job_name=job_name,
        backup_type=backup_type,
        started_at=started_at,
        file_path=out_path,
        md5=md5_hex,
    )


def backup_mysql(
    job_config: dict[str, Any],
    target_disk: str,
    backup_type: str = "full",
    config: dict[str, Any] | None = None,
) -> BackupResult:
    """MySQL backup.

    Server-to-server mode (SSH + mysqldump + smbclient to storage) is used when
    a configured ``backup_target`` and complete SSH password credentials are
    present. Otherwise the legacy password/key download paths are preserved.
    """
    has_target = bool(config and config.get("backup_target"))
    has_ssh = all(job_config.get(k) for k in ("ssh_host", "ssh_user", "ssh_password"))
    if has_target and has_ssh:
        assert config is not None
        return _backup_mysql_remote(job_config, config)
    if "ssh_password" in job_config:
        return _backup_mysql_password(job_config, target_disk, backup_type)
    return _backup_mysql_keyauth(job_config, target_disk, backup_type)


def _backup_mysql_remote(job_config: dict[str, Any], config: dict[str, Any]) -> BackupResult:
    """Run a full MySQL dump on Linux and push it directly to Windows storage."""

    import importlib

    started_at = time.time()
    job_name = _safe_job_name(job_config)
    date = _date_stamp()
    filename = f"{job_name}_{date}_full.sql.gz"
    temp_data = str(job_config.get("temp_data", "/tmp")).rstrip("/") or "/tmp"
    remote_dump = f"{temp_data}/{filename}"
    remote_md5 = remote_dump + ".md5"
    ssh_conn: Any | None = None
    store_conn: Any | None = None

    try:
        connector_mod = importlib.import_module("connector")
        utils_mod = importlib.import_module("utils")
    except ImportError as exc:
        return _result(
            success=False,
            job_name=job_name,
            backup_type="full",
            started_at=started_at,
            error_msg=f"import failed: {exc}",
        )

    target = config.get("backup_target", {})
    try:
        ssh_host = str(job_config["ssh_host"])
        ssh_user = str(job_config["ssh_user"])
        ssh_password = str(job_config["ssh_password"])
        str(job_config["mysql_user"])
    except KeyError as err:
        return _result(
            success=False,
            job_name=job_name,
            backup_type="full",
            started_at=started_at,
            error_msg=f"missing mysql field: {err}",
        )

    try:
        ssh_conn = connector_mod.SSHPasswordConnector(
            host=ssh_host,
            username=ssh_user,
            password=ssh_password,
            port=int(job_config.get("ssh_port", 22)),
            max_retries=3,
            retry_delay=30,
        )
        store_conn = utils_mod.create_target_connector(config)
        if store_conn is None:
            return _result(
                success=False,
                job_name=job_name,
                backup_type="full",
                started_at=started_at,
                error_msg="cannot create storage connector",
            )
        assert ssh_conn is not None

        dump_cmd = _build_mysql_dump_command(job_config, remote_dump, remote_md5)
        code, _out, err = ssh_conn.exec_command(dump_cmd)
        if code != 0:
            return _result(
                success=False,
                job_name=job_name,
                backup_type="full",
                started_at=started_at,
                error_msg=f"mysqldump failed: {(err or '')[:300]}",
            )

        code, md5_out, err = ssh_conn.exec_command(f"cat {shlex.quote(remote_md5)}")
        src_md5 = (md5_out.strip().split() or [""])[0].lower()
        if code != 0 or len(src_md5) != 32:
            return _result(
                success=False,
                job_name=job_name,
                backup_type="full",
                started_at=started_at,
                error_msg=f"source md5 failed: {(err or md5_out)[:200]}",
            )

        target_drive = utils_mod.get_remote_target_disk(
            store_conn,
            min_size_tb=float(target.get("min_size_tb", 2)),
            min_free_gb=float(target.get("min_free_gb", 100)),
        )
        if not target_drive:
            return _result(
                success=False,
                job_name=job_name,
                backup_type="full",
                started_at=started_at,
                error_msg="no storage disk with >2TB total and >100GB free",
            )

        base_path = str(target.get("base_path", "E:\\Backups"))
        base_sub = base_path.split(":", 1)[1].lstrip("\\") if ":" in base_path else base_path.strip("\\")
        drive_letter = str(target_drive).rstrip("\\").rstrip(":")
        admin_share = f"{drive_letter}$"
        storage_ip = str(target["host"])
        store_domain = str(target.get("domain", ""))
        store_user = str(target.get("username", ""))
        store_pw = str(target.get("password", ""))
        smb_user = f"{store_domain}\\{store_user}" if store_domain else store_user
        rel_dir = f"{base_sub}\\{job_name}"
        smb_script = (
            f'prompt OFF; mkdir "{base_sub}"; mkdir "{rel_dir}"; cd "{rel_dir}"; '
            f'put {remote_dump} "{filename}"; put {remote_md5} "{filename}.md5"'
        )
        smb_cmd = (
            f"smbclient //{storage_ip}/{admin_share} -U {shlex.quote(smb_user + '%' + store_pw)} "
            f"-c {shlex.quote(smb_script)} 2>&1; echo SMB_DONE"
        )
        _code, smb_out, _err = ssh_conn.exec_command(smb_cmd)
        if "NT_STATUS" in (smb_out or "") and "putting file" not in (smb_out or "").lower():
            bad_lines = [line for line in smb_out.splitlines() if "NT_STATUS" in line and "COLLISION" not in line]
            if bad_lines:
                return _result(
                    success=False,
                    job_name=job_name,
                    backup_type="full",
                    started_at=started_at,
                    error_msg=f"smbclient push failed: {bad_lines[0][:200]}",
                )

        dest = f"{drive_letter}:\\{base_sub}\\{job_name}\\{filename}"
        code, dst_md5_out, err = store_conn.exec_command(
            f"(Get-FileHash -Algorithm MD5 {_ps_single_quote(dest)}).Hash.ToLower()"
        )
        dst_md5 = dst_md5_out.strip().splitlines()[-1].strip() if dst_md5_out.strip() else ""
        if code != 0 or dst_md5 != src_md5:
            return _result(
                success=False,
                job_name=job_name,
                backup_type="full",
                started_at=started_at,
                error_msg=f"storage md5 mismatch src={src_md5} dst={dst_md5}",
            )

        ssh_conn.exec_command(f"rm -f {shlex.quote(remote_dump)} {shlex.quote(remote_md5)}")

        result = _result(
            success=True,
            job_name=job_name,
            backup_type="full",
            started_at=started_at,
            file_path=dest,
            md5=dst_md5,
        )
        result["on_storage"] = True
        return result
    except Exception as exc:  # noqa: BLE001 - executor must convert all failures into BackupResult.
        return _result(
            success=False,
            job_name=job_name,
            backup_type="full",
            started_at=started_at,
            error_msg=f"unexpected: {exc}",
        )
    finally:
        if ssh_conn is not None:
            try:
                ssh_conn.close()
            except Exception:
                pass
        if store_conn is not None:
            try:
                store_conn.close()
            except Exception:
                pass


def _build_mysql_dump_command(
    job_config: dict[str, Any],
    remote_out: str,
    remote_md5: str,
) -> str:
    """Build the remote mysqldump shell command.

    Supports separate MySQL credentials decoupled from SSH login, configurable
    native or Docker-wrapped mysql/mysqldump paths, explicit database lists,
    all-database dumps with exclude filters, and the legacy single ``database``
    field.
    """

    mysqldump = str(job_config.get("mysqldump_path", "mysqldump"))
    mysql_bin = str(job_config.get("mysql_path", "mysql"))
    mysql_host = str(job_config.get("mysql_host", "127.0.0.1"))
    mysql_port = int(job_config.get("mysql_port", 3306))
    mysql_user = str(job_config["mysql_user"])
    mysql_password = str(job_config.get("mysql_password", ""))

    host_arg = f"-h{shlex.quote(mysql_host)}"
    port_arg = f"-P{mysql_port}"
    user_arg = f"-u{shlex.quote(mysql_user)}"
    pw_arg = ("-p" + shlex.quote(mysql_password)) if mysql_password else ""
    conn = " ".join(p for p in (host_arg, port_arg, user_arg, pw_arg) if p)

    dump_opts = "--single-transaction --routines --triggers --set-gtid-purged=OFF"

    databases = [str(db) for db in list(job_config.get("databases") or [])]
    if not databases and job_config.get("database"):
        databases = [str(job_config["database"])]
    exclude = [
        str(db)
        for db in list(
            job_config.get("exclude_dbs")
            or ["mysql", "information_schema", "performance_schema", "sys"]
        )
    ]

    if databases:
        db_args = "--databases " + " ".join(shlex.quote(d) for d in databases)
        dump = f"{mysqldump} {conn} {dump_opts} {db_args}"
    else:
        excl = "|".join(re.escape(d) for d in exclude)
        list_cmd = f"{mysql_bin} {conn} -N -e 'SHOW DATABASES'"
        dump = (
            f"DBS=$({list_cmd} 2>/dev/null | grep -Ev \"^({excl})$\" | tr '\\n' ' '); "
            f"{mysqldump} {conn} {dump_opts} --databases $DBS"
        )

    return (
        f"{dump} | gzip > {shlex.quote(remote_out)} "
        f"&& md5sum {shlex.quote(remote_out)} > {shlex.quote(remote_md5)}"
    )


def _backup_mysql_password(
    job_config: dict[str, Any],
    target_disk: str,
    backup_type: str = "full",
) -> BackupResult:
    """V2: password-based SSH via SSHPasswordConnector."""

    started_at = time.time()
    job_name = _safe_job_name(job_config)
    local_path = _backup_path(target_disk, job_name, "mysql", backup_type, "sql.gz")
    local_md5_path = local_path + ".md5"
    temp_data = str(job_config.get("temp_data", "/tmp")).rstrip("/") or "/tmp"
    remote_path = f"{temp_data}/{job_name}_{_date_stamp()}_{_suffix_for('mysql', backup_type)}.sql.gz"
    remote_md5_path = remote_path + ".md5"

    try:
        ssh_host = str(job_config["ssh_host"])
        ssh_user = str(job_config["ssh_user"])
        ssh_password = str(job_config["ssh_password"])
        str(job_config["mysql_user"])
    except KeyError as err:
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"missing mysql config field: {err}",
        )

    last_error = "mysql backup failed"
    for attempt in range(1, 4):
        connector = SSHPasswordConnector(
            host=ssh_host,
            username=ssh_user,
            password=ssh_password,
            port=int(job_config.get("ssh_port", 22)),
            max_retries=3,
            retry_delay=30,
        )
        try:
            quoted_remote = shlex.quote(remote_path)
            quoted_remote_md5 = shlex.quote(remote_md5_path)
            cmd = _build_mysql_dump_command(job_config, remote_path, remote_md5_path)
            exit_code, _stdout, stderr = connector.exec_command(cmd)
            if exit_code != 0:
                last_error = stderr.strip() or f"remote mysqldump failed with exit status {exit_code}"
                raise RuntimeError(last_error)

            if not connector.download_file(remote_path, local_path):
                last_error = f"SFTP download failed for {remote_path}"
                raise RuntimeError(last_error)
            if not connector.download_file(remote_md5_path, local_md5_path):
                last_error = f"SFTP download failed for {remote_md5_path}"
                raise RuntimeError(last_error)

            with open(local_md5_path, "r", encoding="utf-8") as fh:
                remote_md5 = fh.read().strip().split()[0]
            local_md5 = _compute_md5_stream(local_path)
            if remote_md5 != local_md5:
                last_error = f"mysql backup md5 mismatch: remote={remote_md5} local={local_md5}"
                _cleanup_paths(local_path, local_md5_path)
                raise RuntimeError(last_error)

            _write_md5_sidecar(local_path, local_md5)
            connector.exec_command(f"rm -f {quoted_remote} {quoted_remote_md5}")
            return _result(
                success=True,
                job_name=job_name,
                backup_type=backup_type,
                started_at=started_at,
                file_path=local_path,
                md5=local_md5,
            )
        except Exception as err:  # noqa: BLE001 - executor must convert all failures into BackupResult.
            last_error = str(err) or err.__class__.__name__
        finally:
            connector.close()

        _cleanup_paths(local_path, local_md5_path)
        if attempt < 3:
            time.sleep(30)

    return _result(
        success=False,
        job_name=job_name,
        backup_type=backup_type,
        started_at=started_at,
        error_msg=last_error,
    )


def _backup_mysql_keyauth(
    job_config: dict[str, Any],
    target_disk: str,
    backup_type: str = "full",
) -> BackupResult:
    """V1 (legacy): key-file-based SSH via paramiko directly."""

    started_at = time.time()
    job_name = _safe_job_name(job_config)
    local_path = _backup_path(target_disk, job_name, "mysql", backup_type, "sql.gz")
    local_md5_path = local_path + ".md5"
    remote_path = f"/tmp/{job_name}_{_date_stamp()}_{_suffix_for('mysql', backup_type)}.sql.gz"
    remote_md5_path = remote_path + ".md5"

    try:
        import paramiko  # type: ignore[reportMissingModuleSource]
        from paramiko.ssh_exception import NoValidConnectionsError  # type: ignore[reportMissingModuleSource]
    except ImportError as err:
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"paramiko not installed: {err}",
        )

    try:
        ssh_host = str(job_config["ssh_host"])
        ssh_user = str(job_config["ssh_user"])
        ssh_key = str(job_config["ssh_key"])
        database = str(job_config["database"])
        mysql_user = str(job_config["mysql_user"])
    except KeyError as err:
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"missing mysql config field: {err}",
        )

    last_error = "mysql backup failed"
    for attempt in range(1, 4):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        sftp = None
        try:
            client.connect(
                ssh_host,
                username=ssh_user,
                key_filename=ssh_key,
                timeout=15,
                banner_timeout=15,
                auth_timeout=15,
            )
            quoted_remote = shlex.quote(remote_path)
            quoted_remote_md5 = shlex.quote(remote_md5_path)
            cmd = (
                "mysqldump --single-transaction --routines --triggers "
                f"--set-gtid-purged=OFF -u {shlex.quote(mysql_user)} {shlex.quote(database)} "
                f"| gzip > {quoted_remote} && md5sum {quoted_remote} > {quoted_remote_md5}"
            )
            _stdin, stdout, stderr = client.exec_command(cmd)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                last_error = stderr.read().decode("utf-8", errors="replace").strip() or (
                    f"remote mysqldump failed with exit status {exit_status}"
                )
                raise RuntimeError(last_error)

            sftp = client.open_sftp()
            sftp.get(remote_path, local_path)
            sftp.get(remote_md5_path, local_md5_path)

            with open(local_md5_path, "r", encoding="utf-8") as fh:
                remote_md5 = fh.read().strip().split()[0]
            local_md5 = _compute_md5_stream(local_path)
            if remote_md5 != local_md5:
                last_error = f"mysql backup md5 mismatch: remote={remote_md5} local={local_md5}"
                _cleanup_paths(local_path, local_md5_path)
                raise RuntimeError(last_error)

            _write_md5_sidecar(local_path, local_md5)
            try:
                sftp.remove(remote_path)
                sftp.remove(remote_md5_path)
            except OSError:
                pass
            return _result(
                success=True,
                job_name=job_name,
                backup_type=backup_type,
                started_at=started_at,
                file_path=local_path,
                md5=local_md5,
            )
        except NoValidConnectionsError as err:
            last_error = f"ssh connection failed: {err}"
        except Exception as err:  # noqa: BLE001 - executor must convert all failures into BackupResult.
            last_error = str(err) or err.__class__.__name__
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass
            try:
                client.close()
            except Exception:
                pass

        _cleanup_paths(local_path, local_md5_path)
        if attempt < 3:
            time.sleep(30)

    return _result(
        success=False,
        job_name=job_name,
        backup_type=backup_type,
        started_at=started_at,
        error_msg=last_error,
    )


def backup_sqlite(
    job_config: dict[str, Any],
    target_disk: str,
    backup_type: str = "full",
) -> BackupResult:
    """Hot-copy a SQLite database, gzip it, and write an MD5 sidecar."""

    started_at = time.time()
    job_name = _safe_job_name(job_config)
    out_path = _backup_path(target_disk, job_name, "sqlite", backup_type, "db.gz")
    tmp_db: str | None = None

    try:
        source_path = str(job_config["db_path"])
        if not os.path.exists(source_path):
            raise FileNotFoundError(source_path)

        # SQLite has no native differential backup here; incremental requests still
        # produce a consistent full hot snapshot with the required *_incr.db.gz name.
        fd, tmp_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        src_conn = sqlite3.connect(source_path)
        try:
            dst_conn = sqlite3.connect(tmp_db)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

        with open(tmp_db, "rb") as f_in, gzip.open(out_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

        md5_hex = _compute_md5_stream(out_path)
        _write_md5_sidecar(out_path, md5_hex)
        return _result(
            success=True,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            file_path=out_path,
            md5=md5_hex,
        )
    except FileNotFoundError as err:
        _cleanup_paths(out_path, out_path + ".md5")
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"sqlite source db missing: {err}",
        )
    except Exception as err:  # noqa: BLE001 - executor API returns failures instead of raising.
        _cleanup_paths(out_path, out_path + ".md5")
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=str(err) or err.__class__.__name__,
        )
    finally:
        if tmp_db:
            _cleanup_paths(tmp_db)


def _is_skippable_dir(path: str) -> bool:
    name = os.path.basename(path)
    return name == "__pycache__" or name.startswith(".") or name.startswith("~")


def _is_skippable_file(path: str) -> bool:
    name = os.path.basename(path)
    return (
        name.startswith(".")
        or name.startswith("~")
        or name.endswith("~")
        or name.endswith(".tmp")
        or name.endswith(".temp")
    )


def _iter_source_files(source_dirs: list[str]) -> list[str]:
    files: list[str] = []
    for source_dir in source_dirs:
        if not os.path.isdir(source_dir):
            raise FileNotFoundError(source_dir)
        for root, dirnames, filenames in os.walk(source_dir):
            dirnames[:] = [d for d in dirnames if not _is_skippable_dir(os.path.join(root, d))]
            for filename in filenames:
                path = os.path.join(root, filename)
                if _is_skippable_file(path):
                    continue
                files.append(path)
    return sorted(files)


def _manifest_path(target_disk: str, job_name: str) -> str:
    return os.path.join(_ensure_out_dir(target_disk), f"{job_name}_manifest.json")


def _load_file_manifest(path: str) -> dict[str, float]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): float(v) for k, v in data.items() if isinstance(v, int | float)}


def _write_file_manifest(path: str, mtimes: dict[str, float]) -> None:
    fd, tmp_path = tempfile.mkstemp(dir=str(Path(path).parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(mtimes, fh, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_path, path)
    except Exception:
        _cleanup_paths(tmp_path)
        raise


def _archive_name(path: str, source_dirs: list[str]) -> str:
    for source_dir in source_dirs:
        try:
            common = os.path.commonpath([os.path.abspath(path), os.path.abspath(source_dir)])
        except ValueError:
            continue
        if common == os.path.abspath(source_dir):
            base = os.path.basename(os.path.abspath(source_dir)) or "source"
            rel = os.path.relpath(path, source_dir)
            return str(Path(base) / rel).replace("\\", "/")
    return os.path.basename(path)


def backup_files(
    job_config: dict[str, Any],
    target_disk: str,
    backup_type: str = "full",
) -> BackupResult:
    """Create full or incremental ZIP backups for configured source directories."""

    started_at = time.time()
    job_name = _safe_job_name(job_config)
    out_path = _backup_path(target_disk, job_name, "file", backup_type, "zip")
    manifest_json = _manifest_path(target_disk, job_name)

    try:
        raw_source_dirs = job_config["source_dirs"]
        if not isinstance(raw_source_dirs, list) or not raw_source_dirs:
            raise ValueError("source_dirs must be a non-empty list")
        source_dirs = [str(path) for path in raw_source_dirs]

        all_files = _iter_source_files(source_dirs)
        current_mtimes = {path: os.path.getmtime(path) for path in all_files}
        previous_mtimes = _load_file_manifest(manifest_json)

        if backup_type == "full":
            included_files = all_files
        else:
            included_files = [
                path for path in all_files if current_mtimes[path] > previous_mtimes.get(path, -1.0)
            ]

        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
            manifest_lines: list[str] = []
            for path in included_files:
                arcname = _archive_name(path, source_dirs)
                archive.write(path, arcname)
                manifest_lines.append(f"{path}\t{current_mtimes[path]:.6f}")
            archive.writestr("manifest.txt", "\n".join(manifest_lines) + ("\n" if manifest_lines else ""))

        md5_hex = _compute_md5_stream(out_path)
        _write_md5_sidecar(out_path, md5_hex)
        _write_file_manifest(manifest_json, current_mtimes)
        return _result(
            success=True,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            file_path=out_path,
            md5=md5_hex,
        )
    except FileNotFoundError as err:
        _cleanup_paths(out_path, out_path + ".md5")
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"file source missing: {err}",
        )
    except Exception as err:  # noqa: BLE001 - executor API returns failures instead of raising.
        _cleanup_paths(out_path, out_path + ".md5")
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=str(err) or err.__class__.__name__,
        )


# ─── Remote File Backup ───────────────────────────────────────────────────────


def backup_remote_files(
    job_config: dict[str, Any],
    target_disk: str,
    backup_type: str = "full",
) -> BackupResult:
    """
    Backup files from a remote server (Linux or Windows) to target_disk.

    job_config must have: name, os, host, username, password, source_dirs, backup_dir
    For Windows: also winrm_port, ssh_port, domain
    For Linux: also port (SSH port)
    """
    started_at = time.time()
    job_name = _safe_job_name(job_config)
    os_type = str(job_config.get("os", "linux")).lower()

    try:
        import sys

        _src = os.path.dirname(os.path.abspath(__file__))
        if _src not in sys.path:
            sys.path.insert(0, _src)
        from connector import create_connector

        connector = create_connector(job_config)
    except Exception as exc:
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"Failed to create connector: {exc}",
        )

    try:
        if os_type == "linux":
            return _backup_remote_files_linux(job_config, connector, target_disk, backup_type, started_at)
        return _backup_remote_files_windows(job_config, connector, target_disk, backup_type, started_at)
    finally:
        try:
            connector.close()
        except Exception:
            pass


def _last_backup_mtime(target_disk: str, job_name: str, extension: str) -> float:
    """Return newest local backup mtime for this remote file job, or 0 if none exists."""
    out_dir = _ensure_out_dir(target_disk)
    newest = 0.0
    prefix = f"{job_name}_"
    suffix = f".{extension}"
    try:
        for name in os.listdir(out_dir):
            if not name.startswith(prefix) or not name.endswith(suffix):
                continue
            path = os.path.join(out_dir, name)
            if os.path.isfile(path):
                newest = max(newest, os.path.getmtime(path))
    except OSError:
        return 0.0
    return newest


def _ps_single_quote(value: str) -> str:
    """Quote a string as a PowerShell single-quoted literal."""
    return "'" + value.replace("'", "''") + "'"


def _backup_remote_files_linux(
    job_config: dict[str, Any],
    connector: Any,
    target_disk: str,
    backup_type: str,
    started_at: float,
) -> BackupResult:
    """Linux remote file backup via SSH+tar."""
    job_name = _safe_job_name(job_config)
    source_dirs = [str(path) for path in job_config.get("source_dirs", [])]
    if not source_dirs:
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg="No source_dirs configured",
        )

    suffix = "full" if backup_type == "full" else "incr"
    remote_tmp = f"/tmp/{job_name}_{_date_stamp()}_{suffix}.tar.gz"
    local_path = _backup_path(target_disk, job_name, "file", backup_type, "tar.gz")
    local_md5_path = local_path + ".md5"
    remote_md5_path = remote_tmp + ".md5"
    quoted_remote = shlex.quote(remote_tmp)
    quoted_remote_md5 = shlex.quote(remote_md5_path)

    if backup_type == "full":
        dirs_str = " ".join(shlex.quote(path) for path in source_dirs)
        tar_cmd = f"tar -czf {quoted_remote} -- {dirs_str} && md5sum {quoted_remote} > {quoted_remote_md5}"
    else:
        cutoff = _last_backup_mtime(target_disk, job_name, "tar.gz")
        find_parts = [
            f"find {shlex.quote(path)} -type f -newermt @{int(cutoff)} -print0 2>/dev/null" for path in source_dirs
        ]
        find_cmd = " ; ".join(find_parts)
        tar_cmd = (
            f"({find_cmd}) | tar --null -czf {quoted_remote} -T - "
            f"&& md5sum {quoted_remote} > {quoted_remote_md5}"
        )

    exit_code, _out, err = connector.exec_command(tar_cmd)
    if exit_code not in (0, None):
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"Remote tar failed (exit {exit_code}): {err[:200]}",
        )

    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    ok = connector.download_file(remote_tmp, local_path)
    md5_ok = connector.download_file(remote_md5_path, local_md5_path)
    if not ok or not md5_ok:
        _cleanup_paths(local_path, local_md5_path)
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg="SFTP download failed",
        )

    try:
        with open(local_md5_path, "r", encoding="utf-8") as fh:
            remote_md5 = fh.read().strip().split()[0]
        md5_hex = _compute_md5_stream(local_path)
        if remote_md5.lower() != md5_hex.lower():
            raise ValueError(f"MD5 mismatch: remote={remote_md5} local={md5_hex}")
        _write_md5_sidecar(local_path, md5_hex)
    except Exception as exc:
        _cleanup_paths(local_path, local_md5_path)
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"MD5 failed: {exc}",
        )

    connector.exec_command(f"rm -f {quoted_remote} {quoted_remote_md5}")

    return _result(
        success=True,
        job_name=job_name,
        backup_type=backup_type,
        started_at=started_at,
        file_path=local_path,
        md5=md5_hex,
    )


def _backup_remote_files_windows(
    job_config: dict[str, Any],
    connector: Any,
    target_disk: str,
    backup_type: str,
    started_at: float,
) -> BackupResult:
    """Windows remote file backup via WinRM/SSH + Compress-Archive."""
    job_name = _safe_job_name(job_config)
    source_dirs = [str(path) for path in job_config.get("source_dirs", [])]
    if not source_dirs:
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg="No source_dirs configured",
        )

    suffix = "full" if backup_type == "full" else "incr"
    remote_tmp = f"C:\\Temp\\{job_name}_{_date_stamp()}_{suffix}.zip"
    local_path = _backup_path(target_disk, job_name, "file", backup_type, "zip")
    local_md5_path = local_path + ".md5"
    remote_md5_path = remote_tmp + ".md5"
    quoted_remote = _ps_single_quote(remote_tmp)
    quoted_remote_md5 = _ps_single_quote(remote_md5_path)
    source_array = "@(" + ",".join(_ps_single_quote(path) for path in source_dirs) + ")"

    exit_code, _out, err = connector.exec_command(
        "if (-not (Test-Path 'C:\\Temp')) { New-Item -ItemType Directory -Path 'C:\\Temp' | Out-Null }"
    )
    if exit_code not in (0, None):
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"Remote temp setup failed (exit {exit_code}): {err[:200]}",
        )

    if backup_type == "full":
        ps_cmd = (
            f"$sources = {source_array};"
            "$paths = foreach ($source in $sources) { Join-Path $source '*' };"
            f"Compress-Archive -Path $paths -DestinationPath {quoted_remote} -Force;"
            f"(Get-FileHash -Algorithm MD5 -Path {quoted_remote}).Hash.ToLower() | Set-Content -Path {quoted_remote_md5}"
        )
    else:
        cutoff = _last_backup_mtime(target_disk, job_name, "zip")
        ps_cmd = (
            f"$sources = {source_array};"
            f"$cutoff = [DateTimeOffset]::FromUnixTimeSeconds({int(cutoff)}).LocalDateTime;"
            "$files = foreach ($source in $sources) { "
            "Get-ChildItem -Recurse -File -Path $source -ErrorAction SilentlyContinue "
            "| Where-Object { $_.LastWriteTime -gt $cutoff } };"
            "if ($files) { "
            f"Compress-Archive -Path ($files.FullName) -DestinationPath {quoted_remote} -Force "
            "} else { "
            "Add-Type -AssemblyName System.IO.Compression.FileSystem;"
            f"$zip = [System.IO.Compression.ZipFile]::Open({quoted_remote}, 'Create'); $zip.Dispose() "
            "};"
            f"(Get-FileHash -Algorithm MD5 -Path {quoted_remote}).Hash.ToLower() | Set-Content -Path {quoted_remote_md5}"
        )

    exit_code, _out, err = connector.exec_command(ps_cmd)
    if exit_code not in (0, None):
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"Remote compress failed (exit {exit_code}): {err[:200]}",
        )

    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    ok = connector.download_file(remote_tmp, local_path)
    md5_ok = connector.download_file(remote_md5_path, local_md5_path)
    if not ok or not md5_ok or not os.path.exists(local_path):
        _cleanup_paths(local_path, local_md5_path)
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg="File download failed",
        )

    try:
        with open(local_md5_path, "r", encoding="utf-8") as fh:
            remote_md5 = fh.read().strip().split()[0]
        md5_hex = _compute_md5_stream(local_path)
        if remote_md5.lower() != md5_hex.lower():
            raise ValueError(f"MD5 mismatch: remote={remote_md5} local={md5_hex}")
        _write_md5_sidecar(local_path, md5_hex)
    except Exception as exc:
        _cleanup_paths(local_path, local_md5_path)
        return _result(
            success=False,
            job_name=job_name,
            backup_type=backup_type,
            started_at=started_at,
            error_msg=f"MD5 failed: {exc}",
        )

    connector.exec_command(
        f"Remove-Item -Force {quoted_remote},{quoted_remote_md5} -ErrorAction SilentlyContinue"
    )

    return _result(
        success=True,
        job_name=job_name,
        backup_type=backup_type,
        started_at=started_at,
        file_path=local_path,
        md5=md5_hex,
    )


def dispatch_backup(
    job_config: dict[str, Any],
    target_disk: str,
    backup_type: str = "full",
    config: dict[str, Any] | None = None,
) -> BackupResult:
    t = job_config.get("type")
    if t == "file" and "os" in job_config:
        return backup_remote_files(job_config, target_disk, backup_type)
    if t == "sqlserver":
        return backup_sqlserver(job_config, target_disk, backup_type, config=config)
    if t == "mysql":
        return backup_mysql(job_config, target_disk, backup_type, config=config)
    if t == "sqlite":
        return backup_sqlite(job_config, target_disk, backup_type)
    if t == "file":
        return backup_files(job_config, target_disk, backup_type)
    raise ValueError(f"Unknown backup type: {t}")
