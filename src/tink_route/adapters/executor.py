"""Subprocess execution adapter and protocol."""

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
        res = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode, res.stdout, res.stderr
