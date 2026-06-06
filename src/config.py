"""Configuration loading for the backup system."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_ALLOWED_DATABASE_TYPES = {"sqlserver", "mysql", "sqlite", "file"}
_REQUIRED_TOP_LEVEL_KEYS = {
    "databases",
    "email",
    "retention_days",
    "log_path",
    "status_file",
}


def load_config(path: str) -> dict[str, Any]:
    """Load and validate a JSON config file.

    The caller should invoke this again whenever a hot reload is needed.
    """

    config_path = Path(path)
    try:
        raw = config_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError as err:
        raise FileNotFoundError(f"config file not found: {config_path}") from err
    except json.JSONDecodeError as err:
        raise ValueError(f"invalid JSON in config file {config_path}: {err}") from err

    if not isinstance(data, dict):
        raise ValueError("config root must be a JSON object")

    missing = sorted(_REQUIRED_TOP_LEVEL_KEYS - data.keys())
    if missing:
        raise ValueError(f"missing required config keys: {', '.join(missing)}")

    databases = data["databases"]
    if not isinstance(databases, list):
        raise ValueError("databases must be a list")

    for index, entry in enumerate(databases):
        _validate_database_entry(entry, index)

    names = [e.get("name", "") for e in databases]
    if len(names) != len(set(names)):
        raise ValueError("databases[] entries must have unique 'name' values")

    remote_file_sources = data.get("remote_file_sources")
    if remote_file_sources is not None:
        if not isinstance(remote_file_sources, list):
            raise ValueError("remote_file_sources must be a list")
        for index, entry in enumerate(remote_file_sources):
            _validate_remote_file_source_entry(entry, index)

    return data


def _validate_database_entry(entry: Any, index: int) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"databases[{index}] must be an object")

    required = {"name", "type", "enabled", "schedule_time"}
    missing = sorted(required - entry.keys())
    if missing:
        raise ValueError(f"databases[{index}] missing required keys: {', '.join(missing)}")

    db_type = entry["type"]
    if db_type not in _ALLOWED_DATABASE_TYPES:
        raise ValueError(
            f"databases[{index}].type must be one of: {', '.join(sorted(_ALLOWED_DATABASE_TYPES))}"
        )

    if not isinstance(entry["name"], str) or not entry["name"].strip():
        raise ValueError(f"databases[{index}].name must be a non-empty string")
    if not isinstance(entry["enabled"], bool):
        raise ValueError(f"databases[{index}].enabled must be a boolean")
    if not isinstance(entry["schedule_time"], str) or not entry["schedule_time"].strip():
        raise ValueError(f"databases[{index}].schedule_time must be a non-empty string")


def _validate_remote_file_source_entry(entry: Any, index: int) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"remote_file_sources[{index}] must be an object")

    required = {"name", "os", "host"}
    missing = sorted(required - entry.keys())
    if missing:
        raise ValueError(
            f"remote_file_sources[{index}] missing required keys: {', '.join(missing)}"
        )

    if not isinstance(entry["name"], str) or not entry["name"].strip():
        raise ValueError(f"remote_file_sources[{index}].name must be a non-empty string")
    if not isinstance(entry["os"], str) or not entry["os"].strip():
        raise ValueError(f"remote_file_sources[{index}].os must be a non-empty string")
    if not isinstance(entry["host"], str) or not entry["host"].strip():
        raise ValueError(f"remote_file_sources[{index}].host must be a non-empty string")
