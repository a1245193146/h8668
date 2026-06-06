"""main.py — Entry point and orchestrator for the backup system.

Wires together: config → scheduler → executor → validator → alerter
No business logic lives here — pure orchestration.
"""

from __future__ import annotations

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
    return p


# ─── Core cycle ───────────────────────────────────────────────────────────────

def _run_job(job: dict[str, Any], config: dict[str, Any]) -> None:
    """Execute a single backup job: backup → validate → update status → alert on failure."""

    job_name = str(job.get("name", "<unknown>"))
    backup_type = str(job.get("backup_type", "full"))

    # 1. Resolve backup directory. Prefer an explicit job path; otherwise find
    # a qualifying target disk and create a per-job directory there.
    backup_dir = job.get("backup_dir")
    if backup_dir:
        backup_dir = str(backup_dir)
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
    result = _executor.dispatch_backup(job, backup_dir, backup_type)

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
    validation = _validator.validate_backup_file(result)
    if not validation.get("valid"):
        # Retry once
        logger.warning("MD5 failed for %s — retrying backup", job_name)
        result2 = _executor.dispatch_backup(job, backup_dir, backup_type)
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
    else:
        entry["last_incremental"] = now_iso

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

def run_once(config: dict[str, Any], job_filter: str | None = None) -> None:
    """Run one backup cycle: all enabled jobs (or just job_filter) then exit."""

    status_path = str(config.get("status_file", "backup_status.json"))
    status = _utils.read_status(status_path)
    jobs = _scheduler.get_today_schedule(config, status)

    if job_filter:
        jobs = [j for j in jobs if j.get("name") == job_filter]
        if not jobs:
            logger.warning("No enabled job named %r found in config", job_filter)
            return

    for job in jobs:
        _run_job(job, config)

    _check_monthly_drill(config)


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

        if args.once:
            run_once(cfg, args.job)
            return 0

        # Continuous loop
        schedule_loop(cfg, cfg["_config_path"])
        return 0

    finally:
        if not args.dry_run:
            _utils.release_lock(pid_file)


if __name__ == "__main__":
    sys.exit(main())
