"""scheduler.py — Backup schedule decision module.

Determines whether to run full or incremental backups based on:
- Day of week (Sunday → full)
- Chain status (broken → full)
- Last full backup age (>= 7 days → full)
- History presence (no history → full / first run)

No imports from executor.py, validator.py, or alerter.py.
No external scheduling libraries (APScheduler, celery, cron).
"""

from __future__ import annotations

import datetime
import json
import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def should_run_full_backup(job_config: dict, history: dict) -> bool:
    """
    Returns True if today should be a FULL backup for this job.
    Conditions (any):
    - history is empty (no previous backups)
    - today is Sunday (weekday() == 6)
    - history['chain_status'] == 'broken'
    - history['last_full'] is missing or older than 7 days
    Uses UTC time internally.
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # No history at all → first run → full
    if not history:
        return True

    # Sunday → always full
    if now_utc.weekday() == 6:
        return True

    # Broken chain → full
    if history.get("chain_status") == "broken":
        return True

    # last_full missing or older than 7 days → full
    last_full_str = history.get("last_full")
    if not last_full_str:
        return True

    try:
        last_full = datetime.datetime.fromisoformat(
            last_full_str.replace("Z", "+00:00")
        )
        if (now_utc - last_full).days >= 7:
            return True
    except (ValueError, TypeError):
        # Unparseable timestamp → assume stale → full
        logger.warning(
            "should_run_full_backup: cannot parse last_full=%r for job %r — forcing full",
            last_full_str,
            job_config.get("name", "<unknown>"),
        )
        return True

    return False


def should_run_incremental(job_config: dict, history: dict) -> bool:
    """
    Returns True if today should be an INCREMENTAL backup.
    Conditions (all must hold):
    - today is NOT Sunday
    - chain is NOT broken
    - last_full exists and is within this week
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # Must not be Sunday
    if now_utc.weekday() == 6:
        return False

    # Chain must not be broken
    if history.get("chain_status") == "broken":
        return False

    # last_full must exist and be within the last 7 days
    last_full_str = history.get("last_full")
    if not last_full_str:
        return False

    try:
        last_full = datetime.datetime.fromisoformat(
            last_full_str.replace("Z", "+00:00")
        )
        if (now_utc - last_full).days >= 7:
            return False
    except (ValueError, TypeError):
        logger.warning(
            "should_run_incremental: cannot parse last_full=%r for job %r — skipping incremental",
            last_full_str,
            job_config.get("name", "<unknown>"),
        )
        return False

    return True


def get_today_schedule(config: dict, status: dict | None = None) -> list[dict]:
    """
    Returns list of jobs to run today, with backup_type resolved.
    Each item: {**job_config, 'backup_type': 'full'|'incremental', 'history': {...}}
    Only includes enabled jobs.
    Uses UTC now to determine day-of-week.
    status: the current backup_status.json dict (or None if first run)
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    is_sunday = now_utc.weekday() == 6

    # Build name → history dict from status['jobs'] list
    history_by_name: dict[str, dict] = {}
    if status and "jobs" in status:
        for job_hist in status["jobs"]:
            name = job_hist.get("name")
            if name:
                history_by_name[name] = job_hist

    result: list[dict] = []

    for job_config in config.get("databases", []):
        if not job_config.get("enabled", False):
            continue

        job_name = job_config.get("name", "")
        history = history_by_name.get(job_name, {})

        # Resolve backup_type following priority rules
        if is_sunday:
            backup_type = "full"
        elif not history:
            # First run for this job (no history entry)
            backup_type = "full"
        elif history.get("chain_status") == "broken":
            # Broken chain always forces full
            backup_type = "full"
        elif should_run_full_backup(job_config, history):
            backup_type = "full"
        elif should_run_incremental(job_config, history):
            backup_type = "incremental"
        else:
            # Safe fallback: if neither condition matches, do full
            backup_type = "full"

        result.append(
            {
                **job_config,
                "backup_type": backup_type,
                "history": history,
            }
        )

    logger.debug(
        "get_today_schedule: %d enabled jobs, is_sunday=%s",
        len(result),
        is_sunday,
    )
    return result


def run_schedule_loop(
    config: dict,
    executor_fn: Callable[[dict], Any],
    status_path: str,
) -> None:
    """
    Main scheduling loop. Runs forever until KeyboardInterrupt.
    Each minute: checks if any enabled job's schedule_time (HH:MM UTC) matches now.
    If match: calls executor_fn(job_dict) and sleeps until next minute.
    Reloads config from disk on each iteration (hot reload).

    Hot-reload: if config contains '_config_path', the file is re-read every
    iteration so changes take effect without restarting the process.
    """
    config_path: str | None = config.get("_config_path")
    current_config: dict = config

    logger.info(
        "run_schedule_loop: started (config_path=%r, status_path=%r)",
        config_path,
        status_path,
    )

    try:
        while True:
            # --- Hot reload ---
            if config_path:
                try:
                    with open(config_path, "r", encoding="utf-8") as fh:
                        current_config = json.load(fh)
                    logger.debug("run_schedule_loop: config reloaded from %s", config_path)
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "run_schedule_loop: config reload failed (%s) — using cached config",
                        exc,
                    )

            now_utc = datetime.datetime.now(datetime.timezone.utc)
            now_hhmm = now_utc.strftime("%H:%M")

            # Load current status to inform backup-type decisions
            status = _load_status(status_path)
            history_by_name = _build_history_lookup(status)

            for job_config in current_config.get("databases", []):
                if not job_config.get("enabled", False):
                    continue

                if job_config.get("schedule_time", "") != now_hhmm:
                    continue

                job_name = job_config.get("name", "")
                history = history_by_name.get(job_name, {})
                backup_type = _resolve_backup_type(job_config, history, now_utc)

                job_dict: dict = {
                    **job_config,
                    "backup_type": backup_type,
                    "history": history,
                }

                logger.info(
                    "run_schedule_loop: triggering %s backup for job=%r at %s UTC",
                    backup_type,
                    job_name,
                    now_hhmm,
                )
                try:
                    executor_fn(job_dict)
                except Exception as exc:
                    logger.error(
                        "run_schedule_loop: executor raised for job=%r: %s",
                        job_name,
                        exc,
                    )

            # Sleep until the top of the next minute
            sleep_secs = 60 - now_utc.second - now_utc.microsecond / 1_000_000
            logger.debug("run_schedule_loop: sleeping %.1f s until next minute", sleep_secs)
            time.sleep(max(sleep_secs, 1.0))

    except KeyboardInterrupt:
        logger.info("run_schedule_loop: stopped by KeyboardInterrupt")


# ---------------------------------------------------------------------------
# Internal helpers (not part of public API)
# ---------------------------------------------------------------------------

def _load_status(status_path: str) -> dict:
    """Load status JSON from disk, returning empty dict on any failure."""
    try:
        with open(status_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _build_history_lookup(status: dict) -> dict[str, dict]:
    """Convert status['jobs'] list into a name-keyed dict for O(1) lookup."""
    lookup: dict[str, dict] = {}
    for job_hist in status.get("jobs", []):
        name = job_hist.get("name")
        if name:
            lookup[name] = job_hist
    return lookup


def _resolve_backup_type(
    job_config: dict,
    history: dict,
    now_utc: datetime.datetime,
) -> str:
    """Determine 'full' or 'incremental' for a single job given a fixed UTC instant."""
    if now_utc.weekday() == 6:
        return "full"
    if not history:
        return "full"
    if history.get("chain_status") == "broken":
        return "full"
    if should_run_full_backup(job_config, history):
        return "full"
    if should_run_incremental(job_config, history):
        return "incremental"
    return "full"  # safe fallback
