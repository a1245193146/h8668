"""storage_status.py — Build dashboard status by scanning the REAL backup
files on the storage server (single source of truth), per the requirement
that the frontend reads the backup directory structure.
"""
from __future__ import annotations

import datetime
import logging
import os
import re
import sys

logger = logging.getLogger(__name__)


def _ensure_paths() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    for p in (here, os.path.join(root, "vendor")):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


# filename -> backup_type + db_type
_EXT_TYPE = [
    (".bak", "sqlserver"),
    (".sql.gz", "mysql"),
    (".db.gz", "sqlite"),
    (".zip", "file"),
]

# {job}_{YYYYMMDD}_{full|diff|incr}.{ext}
_NAME_RE = re.compile(
    r"^(?P<job>.+)_(?P<date>\d{8})_(?P<kind>full|diff|incr)\.",
    re.IGNORECASE,
)


def _db_type_for(filename: str) -> str:
    low = filename.lower()
    for ext, t in _EXT_TYPE:
        if low.endswith(ext):
            return t
    return "unknown"


def _empty_status() -> dict:
    return {
        "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "disks": [],
        "jobs": [],
        "alerts": [],
    }


def _ps_single_quote(value: str) -> str:
    """Quote a string for use as a PowerShell single-quoted literal."""
    return "'" + value.replace("'", "''") + "'"


def build_status_from_storage(config: dict) -> dict:
    """Scan the storage server's backup tree and return dashboard status.

    Never raises; on any failure returns a valid empty-ish status with an alert.
    """
    _ensure_paths()
    try:
        import utils as _utils
    except ImportError as exc:
        logger.error("storage_status import failed: %s", exc)
        return _empty_status()

    target = config.get("backup_target") or {}
    base_path = str(target.get("base_path", "E:\\Backups"))
    status = _empty_status()

    connector = _utils.create_target_connector(config)
    if connector is None:
        status["alerts"].append(
            {
                "time": status["last_updated"],
                "level": "warn",
                "message": "未配置或无法连接存储服务器(backup_target)，无法读取真实备份目录",
            }
        )
        return status

    try:
        # 1) disks of the storage server (show all drives for the usage chart)
        try:
            status["disks"] = _utils.scan_remote_disks(connector, min_size_tb=0.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scan_remote_disks failed: %s", exc)

        # 2) list every backup file under base_path:
        #    jobdir|filename|size|mtimeUtcIso|hasMd5
        base_literal = _ps_single_quote(base_path)
        ps = (
            f"$base={base_literal}; if (Test-Path $base) {{ "
            "Get-ChildItem -Path $base -Recurse -File | "
            "Where-Object { $_.Name -notlike '*.md5' -and "
            "($_.Name -like '*.bak' -or $_.Name -like '*.sql.gz' -or "
            "$_.Name -like '*.db.gz' -or $_.Name -like '*.zip') } | "
            "ForEach-Object { "
            "$m = if (Test-Path ($_.FullName + '.md5')) {'Y'} else {'N'}; "
            "Write-Output (\"{0}|{1}|{2}|{3}|{4}\" -f $_.Directory.Name, "
            "$_.Name, $_.Length, $_.LastWriteTimeUtc.ToString('o'), $m) } }"
        )
        code, out, err = connector.exec_command(ps)
        if code != 0:
            status["alerts"].append(
                {
                    "time": status["last_updated"],
                    "level": "error",
                    "message": f"扫描存储目录失败: {(err or '')[:200]}",
                }
            )
            return status

        # 3) group records by job
        jobs: dict[str, dict] = {}
        for line in out.splitlines():
            line = line.strip()
            if not line or line.count("|") < 4:
                continue
            job_dir, fname, size_s, mtime_s, has_md5 = line.split("|", 4)
            m = _NAME_RE.match(fname)
            date_iso = None
            kind = "full"
            if m:
                try:
                    d = datetime.datetime.strptime(m.group("date"), "%Y%m%d")
                    date_iso = d.replace(tzinfo=datetime.timezone.utc).isoformat()
                except ValueError:
                    pass
                kind = m.group("kind").lower()
            try:
                size_mb = round(int(size_s) / (1024 * 1024), 2)
            except ValueError:
                size_mb = 0.0
            job_name = (m.group("job") if m else job_dir) or job_dir
            j = jobs.setdefault(
                job_name,
                {
                    "name": job_name,
                    "type": _db_type_for(fname),
                    "last_full": None,
                    "last_incremental": None,
                    "increments_since_full": 0,
                    "chain_status": "unknown",
                    "last_result": "success",
                    "file_path": None,
                    "file_size_mb": 0.0,
                    "md5_verified": False,
                    "duration_seconds": 0,
                    "_latest_mtime": "",
                    "_incremental_dates": [],
                },
            )
            is_full = kind == "full"
            if is_full:
                if (j["last_full"] or "") < (date_iso or ""):
                    j["last_full"] = date_iso
            else:
                if (j["last_incremental"] or "") < (date_iso or ""):
                    j["last_incremental"] = date_iso
                if date_iso:
                    j["_incremental_dates"].append(date_iso)
            # track the newest file overall for size/path/md5/result
            if mtime_s > j["_latest_mtime"]:
                j["_latest_mtime"] = mtime_s
                j["file_path"] = f"{base_path}\\{job_dir}\\{fname}"
                j["file_size_mb"] = size_mb
                j["md5_verified"] = has_md5 == "Y"

        # 4) chain status: a full exists; rolling increments are counted since latest full.
        for j in jobs.values():
            lf = j.get("last_full") or ""
            incremental_dates = j.pop("_incremental_dates", [])
            j["increments_since_full"] = sum(1 for date in incremental_dates if lf and date > lf)
            j["chain_status"] = "intact" if lf else "broken"
            j.pop("_latest_mtime", None)
            if j["chain_status"] == "broken":
                status["alerts"].append(
                    {
                        "time": status["last_updated"],
                        "level": "warn",
                        "message": f"[{j['name']}] 全量备份缺失，链状态: broken",
                    }
                )

        status["jobs"] = sorted(jobs.values(), key=lambda x: x["name"])
        if not status["jobs"]:
            status["alerts"].append(
                {
                    "time": status["last_updated"],
                    "level": "info",
                    "message": f"存储目录 {base_path} 下暂无备份文件",
                }
            )
        return status
    except Exception as exc:  # noqa: BLE001
        logger.error("build_status_from_storage error: %s", exc)
        status["alerts"].append(
            {
                "time": status["last_updated"],
                "level": "error",
                "message": f"读取存储备份状态异常: {exc}",
            }
        )
        return status
    finally:
        try:
            connector.close()
        except Exception:
            pass


def refresh_dashboard_status(config: dict) -> bool:
    """Scan storage and write the status to config['status_file'].

    ``utils.write_status`` mirrors into dashboard/backup_status.json. Returns True
    on successful write.
    """
    _ensure_paths()
    try:
        import utils as _utils
    except ImportError:
        return False
    status = build_status_from_storage(config)
    status_path = str(config.get("status_file", "backup_status.json"))
    try:
        _utils.write_status(status, status_path)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("refresh_dashboard_status write failed: %s", exc)
        return False
