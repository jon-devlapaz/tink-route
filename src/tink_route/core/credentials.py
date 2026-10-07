"""Find the TypeSafe API key. Callers may report where it came from, never the key itself.

Order: the TYPESAFE_API_KEY environment variable, then a private key file, then (macOS only) launchctl.
Agent harnesses often start tools from non-interactive shells that never read ~/.zshrc, so the key file is
the reliable place for it.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

ENV_VAR = "TYPESAFE_API_KEY"


class KeyFileInsecure(Exception):
    """The key file exists but is not a regular file, owned by this user, readable only by them."""


def key_file_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "")
    root = Path(base) if base and Path(base).is_absolute() else Path.home() / ".config"
    return root / "tink-route" / "typesafe_api_key"


def _from_file(path: Path) -> str | None:
    """Open once without following symlinks, then validate and read that same descriptor (no swap window)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError:  # a symlink (ELOOP), or a file its owner cannot read
        raise KeyFileInsecure() from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        owner_ok = not hasattr(os, "getuid") or info.st_uid == os.getuid()
        if not stat.S_ISREG(info.st_mode) or not owner_ok or info.st_mode & 0o077 or not info.st_mode & stat.S_IRUSR:
            raise KeyFileInsecure()
        return handle.read().decode("utf-8").strip() or None


def _from_launchctl() -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(["launchctl", "getenv", ENV_VAR], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value or None


def resolve_api_key() -> tuple[str | None, str | None]:
    """(key, source) with source "env", "file" or "launchctl"; (None, None) when there is no key.

    Raises KeyFileInsecure for a key file that must not be trusted.
    """
    key = os.environ.get(ENV_VAR, "").strip()
    if key:
        return key, "env"
    key = _from_file(key_file_path())
    if key:
        return key, "file"
    key = _from_launchctl()
    if key:
        return key, "launchctl"
    return None, None
