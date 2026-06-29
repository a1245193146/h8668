"""scheduler.py — Backup schedule decision module.

Determines whether to run full or incremental backups based on a rolling
7-run cycle:
- History presence (no history → full / first run)
- Last full presence
- Increments since the last full (6 increments → next run is full)

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
    - history['last_full'] is missing
    - history['increments_since_full'] is >= 6
    """
    if not history:
        return True

    if not history.get("last_full"):
        return True

    try:
        return int(history.get("increments_since_full", 0)) >= 6
    except (ValueError, TypeError):
        logger.warning(
            "should_run_full_backup: cannot parse increments_since_full=%r for job %r — forcing full",
            history.get("increments_since_full"),
            job_config.get("name", "<unknown>"),
        )
        return True


def should_run_incremental(job_config: dict, history: dict) -> bool:
    """
    Returns True if today should be an INCREMENTAL backup.
    Conditions: a full exists and fewer than 6 increments have run after it.
    """
    if not history or not history.get("last_full"):
        return False
    return not should_run_full_backup(job_config, history)


def get_today_schedule(config: dict, status: dict | None = None) -> list[dict]:
    """
    Returns list of jobs to run today, with backup_type resolved.
    Each item: {**job_config, 'backup_type': 'full'|'incremental', 'history': {...}}
    Only includes enabled jobs.
    status: the current backup_status.json dict (or None if first run)
    """
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

        if should_run_full_backup(job_config, history):
            backup_type = "full"
        else:
            backup_type = "incremental"

        result.append(
            {
                **job_config,
                "backup_type": backup_type,
                "history": history,
            }
        )

    logger.debug(
        "get_today_schedule: %d enabled jobs",
        len(result),
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
    if should_run_full_backup(job_config, history):
        return "full"
    return "incremental"
