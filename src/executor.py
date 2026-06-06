"""Backup executor implementations for SQL Server, MySQL, SQLite, and files."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
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
) -> BackupResult:
    """Run SQL Server BACKUP DATABASE through sqlcmd and return a BackupResult."""

    started_at = time.time()
    job_name = _safe_job_name(job_config)
    out_path = _backup_path(target_disk, job_name, "sqlserver", backup_type, "bak")

    try:
        server = str(job_config["server"])
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
) -> BackupResult:
    """Run mysqldump on a remote host over Paramiko SSH and pull it via SFTP."""

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


def dispatch_backup(
    job_config: dict[str, Any],
    target_disk: str,
    backup_type: str = "full",
) -> BackupResult:
    t = job_config.get("type")
    if t == "sqlserver":
        return backup_sqlserver(job_config, target_disk, backup_type)
    if t == "mysql":
        return backup_mysql(job_config, target_disk, backup_type)
    if t == "sqlite":
        return backup_sqlite(job_config, target_disk, backup_type)
    if t == "file":
        return backup_files(job_config, target_disk, backup_type)
    raise ValueError(f"Unknown backup type: {t}")
