"""Subprocess execution adapter and protocol."""

import os
import subprocess
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class SubprocessExecutor(Protocol):
    """Protocol defining process execution interface for dependency injection."""

    def run(self, cmd: list[str], cwd: Path) -> tuple[int, str, str]:
        """Execute command in cwd and return (returncode, stdout, stderr)."""
        ...


class DefaultSubprocessExecutor:
    """Standard subprocess executor wrapping subprocess.run."""

    def run(self, cmd: list[str], cwd: Path) -> tuple[int, str, str]:
        kwargs: dict = {}
        if cmd[:3] == ["tink", "skill", "add"]:
            # Tell tink this add is tink-route's own (ledger-tracked), not a user promotion.
            kwargs["env"] = {**os.environ, "TINK_ROUTE_INSTALL": "1"}
        res = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            **kwargs,
        )
        return res.returncode, res.stdout, res.stderr
