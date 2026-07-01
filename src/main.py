"""main.py — Entry point and orchestrator for the backup system.

Wires together: config → scheduler → executor → validator → alerter
No business logic lives here — pure orchestration.
"""

from __future__ import annotations

import os as _os, sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))      # .../src
_ROOT = _os.path.dirname(_HERE)                            # project root
for _p in (_HERE, _os.path.join(_ROOT, "vendor")):
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.insert(0, _p)

import argparse
import datetime
import logging
import os
import sys
from pathlib import Path
from typing import Any

# ---- Module imports (all src/ siblings) ----
import alerter as _alerter
import config as _config_mod
import executor as _executor
import scheduler as _scheduler
import restore_verifier as _restore_verifier
import storage_status as _storage_status
import utils as _utils
import validator as _validator

logger = logging.getLogger("backup.main")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backup",
        description="Data Backup System — scheduler, executor, validator, alerter",
    )
    p.add_argument(
        "--config",
        default="config.json",
        help="Path to config JSON file (default: config.json)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would run today without executing anything",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Run one backup cycle then exit",
    )
    p.add_argument(
        "--job",
        metavar="NAME",
        help="Only process the named job (use with --once)",
    )
    p.add_argument(
        "--verify-restore",
        action="store_true",
        help="Force run monthly restore verification now",
    )
    p.add_argument(
        "--refresh-status",
        action="store_true",
        help="Scan real storage backup tree and refresh dashboard status now",
    )
    return p


# ─── Core cycle ───────────────────────────────────────────────────────────────

def _run_job(job: dict[str, Any], config: dict[str, Any]) -> None:
    """Execute a single backup job: backup → validate → update status → alert on failure."""

    job_name = str(job.get("name", "<unknown>"))
    backup_type = str(job.get("backup_type", "full"))
    remote_sqlserver = bool(
        job.get("type") == "sqlserver"
        and job.get("host")
        and config.get("backup_target")
    )
    remote_mysql = bool(
        job.get("type") == "mysql"
        and config.get("backup_target")
        and job.get("ssh_host")
        and job.get("ssh_user")
        and job.get("ssh_password")
    )
    remote_s2s = remote_sqlserver or remote_mysql

    # 1. Resolve backup directory. Prefer an explicit job path; otherwise find
    # a qualifying target disk and create a per-job directory there.
    backup_dir = job.get("backup_dir")
    if backup_dir:
        backup_dir = str(backup_dir)
    elif remote_s2s:
        # Server-to-server backups are written on the database host and pushed
        # directly to storage; the orchestrator must not require local staging.
        backup_dir = str(job.get("local_backup_dir", "C:\\sqlbak"))
    else:
        disk_cfg = config.get("disks", {})
        target_disk = _utils.get_target_disk(
            min_size_tb=float(disk_cfg.get("min_size_tb", 2.0)),
            min_free_gb=float(disk_cfg.get("min_free_gb", 100.0)),
        )
        if target_disk is None:
            _alerter.alert_disk_full("<all disks>", 0.0, config)
            logger.error("No target disk available — skipping job %s", job_name)
            _update_status(
                config,
                job_name,
                str(job.get("type", "unknown")),
                backup_type,
                success=False,
                audit={"intact": False, "missing_files": [], "recommendation": "force_full"},
                result={"md5": None, "error_msg": "no target disk available"},
            )
            return
        backup_dir = str(Path(target_disk) / job_name)

    if not remote_s2s:
        try:
            os.makedirs(backup_dir, exist_ok=True)
        except OSError as exc:
            error_msg = f"cannot create backup directory {backup_dir!r}: {exc}"
            _alerter.alert_backup_failed(job_name, error_msg, config)
            _update_status(
                config,
                job_name,
                str(job.get("type", "unknown")),
                backup_type,
                success=False,
                audit={"intact": False, "missing_files": [], "recommendation": "force_full"},
                result={"md5": None, "error_msg": error_msg},
            )
            return

    # 2. Chain audit + fuse check
    if remote_sqlserver:
        # Backups live on the storage server, not the orchestrator's backup_dir.
        # Audit the real chain on storage so weekday differentials are allowed.
        _store_conn = _utils.create_target_connector(config)
        if _store_conn is not None:
            _base = str(config.get("backup_target", {}).get("base_path", "E:\\Backups"))
            audit_remote = getattr(_validator, "audit_backup_chain_remote")
            audit = audit_remote(job, _store_conn, _base)
            try:
                _store_conn.close()
            except Exception:
                pass
        else:
            audit = {"intact": True, "missing_files": [], "last_valid_date": None,
                     "recommendation": "proceed_incremental"}
    elif remote_mysql:
        audit = {"intact": True, "missing_files": [], "last_valid_date": None,
                 "recommendation": "proceed_incremental"}
    else:
        audit = _validator.audit_backup_chain(job, backup_dir)
    fuse = _validator.fuse_check(audit)
    if fuse == "force_full" and backup_type != "full":
        logger.warning(
            "Chain broken for %s — overriding %s → full backup",
            job_name,
            backup_type,
        )
        _alerter.alert_chain_broken(job_name, audit.get("missing_files", []), config)
        backup_type = "full"

    # 3. Execute backup
    logger.info("Starting %s backup for job=%s → %s", backup_type, job_name, backup_dir)
    result = _executor.dispatch_backup(job, backup_dir, backup_type, config=config)

    if not result.get("success"):
        _alerter.alert_backup_failed(
            job_name,
            str(result.get("error_msg") or "unknown error"),
            config,
        )
        _update_status(
            config,
            job_name,
            str(job.get("type", "unknown")),
            backup_type,
            success=False,
            audit=audit,
            result=result,
        )
        return

    # 4. MD5 validation
    if result.get("on_storage"):
        validation = {"valid": True, "actual_md5": result.get("md5")}
    else:
        validation = _validator.validate_backup_file(result)
    if not validation.get("valid"):
        # Retry once
        logger.warning("MD5 failed for %s — retrying backup", job_name)
        result2 = _executor.dispatch_backup(job, backup_dir, backup_type, config=config)
        if result2.get("on_storage"):
            validation2 = {"valid": True, "actual_md5": result2.get("md5")}
        else:
            validation2 = _validator.validate_backup_file(result2)
        if not validation2.get("valid"):
            _alerter.alert_md5_mismatch(
                job_name,
                str(validation2.get("expected_md5") or "?"),
                str(validation2.get("actual_md5") or "?"),
                config,
            )
            _update_status(
                config,
                job_name,
                str(job.get("type", "unknown")),
                backup_type,
                success=False,
                audit=audit,
                result=result2,
            )
            return
        result = result2
        validation = validation2

    logger.info(
        "Backup OK: job=%s file=%s md5=%s",
        job_name,
        result.get("file_path"),
        validation.get("actual_md5") or result.get("md5"),
    )
    _update_status(
        config,
        job_name,
        str(job.get("type", "unknown")),
        backup_type,
        success=True,
        audit=audit,
        result=result,
    )

    # Push to remote storage if backup_target configured
    if result.get("success") and result.get("file_path") and not result.get("on_storage"):
        backup_target = config.get("backup_target", {})
        if backup_target:
            _push_to_remote_target(str(result["file_path"]), job_name, config)


def _push_to_remote_target(local_file: str, job_name: str, config: dict) -> None:
    """Push a local backup file to the configured remote backup_target."""
    target = config.get("backup_target", {})
    if not target:
        return
    try:
        connector = _utils.create_target_connector(config)
        if connector is None:
            logger.warning("_push_to_remote_target: could not create connector for %s", job_name)
            return
        base_path = target.get("base_path", "E:\\Backups")
        remote_dir = f"{base_path}\\{job_name}"
        remote_path = f"{remote_dir}\\{os.path.basename(local_file)}"

        # Ensure remote dir exists (Windows)
        connector.exec_command(
            f"if (-not (Test-Path '{remote_dir}')) {{ New-Item -ItemType Directory -Force '{remote_dir}' }}"
        )
        ok = _utils.push_file_to_remote(local_file, remote_path, connector)
        if ok:
            logger.info("_push_to_remote_target: %s → %s OK", local_file, remote_path)
        else:
            logger.error("_push_to_remote_target: push failed for %s", local_file)
        connector.close()
    except Exception as exc:
        logger.error("_push_to_remote_target failed for %s: %s", job_name, exc)


def _update_status(
    config: dict[str, Any],
    job_name: str,
    job_type: str,
    backup_type: str,
    *,
    success: bool,
    audit: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Atomically update backup_status.json after a job completes."""

    status_path = str(config.get("status_file", "backup_status.json"))
    status = _utils.read_status(status_path)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    status.setdefault("disks", [])
    status.setdefault("jobs", [])
    status.setdefault("alerts", [])
    status["last_updated"] = now_iso

    # Update or insert job entry
    jobs: list[dict[str, Any]] = status["jobs"]
    entry: dict[str, Any] | None = next(
        (j for j in jobs if j.get("name") == job_name),
        None,
    )
    if entry is None:
        entry = {"name": job_name, "type": job_type}
        jobs.append(entry)

    entry["last_result"] = "success" if success else "failed"
    entry["chain_status"] = "intact" if audit.get("intact") else "broken"
    entry["md5_verified"] = bool(result.get("md5")) and success

    if backup_type == "full":
        entry["last_full"] = now_iso
        entry["increments_since_full"] = 0
    else:
        entry["last_incremental"] = now_iso
        entry["increments_since_full"] = int(entry.get("increments_since_full", 0)) + 1

    if success:
        entry["file_path"] = result.get("file_path")
        entry["file_size_mb"] = result.get("file_size_mb", 0.0)
        entry["duration_seconds"] = result.get("duration_seconds", 0.0)
    else:
        entry["error_msg"] = result.get("error_msg", "unknown error")

    # Update disk snapshot
    disks = _utils.scan_large_disks(min_size_tb=0.001)  # include all drives for display
    status["disks"] = disks

    _utils.write_status(status, status_path)
    logger.debug("Status updated → %s", status_path)


# ─── Monthly drill reminder ────────────────────────────────────────────────────

def _check_monthly_drill(config: dict[str, Any]) -> None:
    """On the 1st of each month, log a reminder to run the recovery drill."""

    if datetime.datetime.now(datetime.timezone.utc).day == 1:
        _alerter.alert_monthly_drill_reminder(config)


# ─── Main entry points ────────────────────────────────────────────────────────

def run_once(config: dict[str, Any], job_filter: str | None = None, skip_verification: bool = False) -> None:
    """Run one backup cycle: all enabled jobs (or just job_filter) then exit."""

    status_path = str(config.get("status_file", "backup_status.json"))
    status = _utils.read_status(status_path)
    jobs = _scheduler.get_today_schedule(config, status)

    # Also dispatch remote_file_sources jobs
    for src in config.get("remote_file_sources", []):
        if not src.get("enabled", False):
            continue
        # Add backup_type using same logic as scheduler
        history = next((j for j in status.get("jobs", []) if j.get("name") == src.get("name")), {})
        backup_type = "full" if _scheduler.should_run_full_backup(src, history) else "incremental"
        jobs.append({**src, "backup_type": backup_type, "history": history})

    if job_filter:
        jobs = [j for j in jobs if j.get("name") == job_filter]
        if not jobs:
            logger.warning("No enabled job named %r found in config", job_filter)

    for job in jobs:
        _run_job(job, config)

    _check_monthly_drill(config)

    # Monthly restore verification
    if not skip_verification and _restore_verifier.is_verification_day(config):
        logger.info("Monthly verification day — running restore verification")
        _restore_verifier.run_monthly_verification(config)

    # Refresh dashboard from the real storage backup tree
    try:
        _storage_status.refresh_dashboard_status(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard refresh failed: %s", exc)


def dry_run(config: dict[str, Any], job_filter: str | None = None) -> None:
    """Print what would run today without executing anything."""

    status_path = str(config.get("status_file", "backup_status.json"))
    status = _utils.read_status(status_path)
    jobs = _scheduler.get_today_schedule(config, status)

    if job_filter:
        jobs = [j for j in jobs if j.get("name") == job_filter]

    print("=" * 60)
    print("  DRY RUN — Backup Schedule for Today")
    print(
        "  UTC: "
        f"{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    print("=" * 60)

    if not jobs:
        print("  No jobs scheduled for today.")
    for job in jobs:
        marker = "[ DRY RUN ]"
        print(
            f"  {marker} job={job['name']!r:30s} type={job['type']:12s} "
            f"backup={job['backup_type']:15s} time={job.get('schedule_time', '??')}"
        )

    # Also surface disabled jobs so operators can see all configured jobs at a glance.
    enabled_names = {j["name"] for j in jobs}
    disabled_jobs = [
        db for db in config.get("databases", [])
        if not db.get("enabled", False)
        and db.get("name") not in enabled_names
        and (not job_filter or db.get("name") == job_filter)
    ]
    for job in disabled_jobs:
        marker = "[DISABLED ]"
        print(
            f"  {marker} job={job['name']!r:30s} type={job['type']:12s} "
            f"backup={'(skipped)':15s} time={job.get('schedule_time', '??')}"
        )

    # Show remote file sources
    for src in config.get("remote_file_sources", []):
        enabled = src.get("enabled", False)
        marker = "[ DRY RUN ]" if enabled else "[DISABLED ]"
        print(f"  {marker} remote={src.get('name','?'):25s} os={src.get('os','?'):8s} "
              f"time={src.get('schedule_time','??')}")

    print("=" * 60)


def schedule_loop(config: dict[str, Any], config_path: str) -> None:
    """Run the continuous scheduling loop (hot-reload config each minute)."""

    config["_config_path"] = config_path

    def executor_fn(job: dict[str, Any]) -> None:
        fresh_cfg = _config_mod.load_config(config_path)
        _run_job(job, fresh_cfg)

    _scheduler.run_schedule_loop(
        config,
        executor_fn,
        str(config.get("status_file", "backup_status.json")),
    )


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    # Load config
    try:
        cfg = _config_mod.load_config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] Cannot load config {args.config!r}: {exc}", file=sys.stderr)
        return 1

    # Store config path for hot-reload
    cfg["_config_path"] = os.path.abspath(args.config)

    # Setup logger. Dry-run intentionally avoids logger setup so schedule
    # inspection does not create log files.
    if not args.dry_run:
        _alerter.setup_logger(cfg)

    # PID lock (skip in dry-run to allow concurrent inspection)
    pid_file = str(cfg.get("pid_file", "backup.pid"))
    if not args.dry_run:
        if not _utils.acquire_lock(pid_file):
            print("[ERROR] Another backup instance is already running.", file=sys.stderr)
            return 2

    try:
        if args.dry_run:
            dry_run(cfg, args.job)
            return 0

        if args.refresh_status:
            ok = _storage_status.refresh_dashboard_status(cfg)
            print("[OK] Dashboard status refreshed from storage" if ok else "[ERROR] Dashboard status refresh failed")
            return 0 if ok else 1

        if args.once or args.verify_restore:
            if args.once:
                run_once(cfg, args.job, skip_verification=args.verify_restore)
            if args.verify_restore:
                logger.info("--verify-restore: forcing restore verification now")
                results = _restore_verifier.run_monthly_verification(cfg, force=True)
                for r in results:
                    status_str = "OK" if r["success"] else "FAILED"
                    print(f"  [{status_str}] {r['db_name']:12s} tables={r['tables_count']} "
                          f"duration={r['duration_seconds']:.1f}s "
                          f"error={r.get('error_msg','')[:60] if not r['success'] else ''}")
            return 0

        # Continuous loop
        schedule_loop(cfg, cfg["_config_path"])
        return 0

    finally:
        if not args.dry_run:
            _utils.release_lock(pid_file)


if __name__ == "__main__":
    sys.exit(main())
