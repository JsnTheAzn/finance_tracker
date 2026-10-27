"""Loads settings from the project's git-ignored .env file. See docs/phase3.md."""

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ConfigError(Exception):
    """A required setting is missing or invalid. The message never contains secret values."""


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> None:
    """Load KEY=VALUE lines from a .env file without overriding variables already set."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not ENV_NAME_PATTERN.fullmatch(key):
            continue
        os.environ.setdefault(key, _parse_env_value(value.strip()))


def _parse_env_value(value: str) -> str:
    """Handle quotes and trailing ' # comments' in a .env value."""
    if value[:1] in ("'", '"'):
        closing = value.find(value[0], 1)
        if closing != -1:
            return value[1:closing]
    for separator in (" #", "\t#"):
        if separator in value:
            value = value.split(separator, 1)[0]
    return value.strip()


def project_path(value: str) -> Path:
    """Resolve a path from .env relative to the project root (absolute paths are kept)."""
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path
