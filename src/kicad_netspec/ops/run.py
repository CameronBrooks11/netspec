"""Run a command the user named, and report how it went.

The only module outside the oracle permitted to spawn a process (DECISIONS D4.1), and it
must never invoke a KiCad binary -- enforced by a test. What it runs is whatever `guard`
was pointed at: an agent, a generator, a build script, anything.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["CommandResult", "run_command"]


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    failed_to_start: bool = False
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.failed_to_start

    def __str__(self) -> str:
        if self.failed_to_start:
            return f"could not run {' '.join(self.argv)}: {self.message}"
        return f"{' '.join(self.argv)} exited {self.returncode}"


def run_command(argv: Sequence[str], *, cwd: str | None = None) -> CommandResult:
    """Run a command, letting its output through to the caller's terminal.

    Output is deliberately not captured: the point of `guard` is that you watch the tool
    work, and netspec adjudicates afterwards.
    """
    if not argv:
        return CommandResult(
            argv=(), returncode=0, failed_to_start=True, message="no command given"
        )
    try:
        done = subprocess.run(list(argv), cwd=cwd, check=False)  # noqa: S603
    except OSError as exc:
        return CommandResult(
            argv=tuple(argv), returncode=127, failed_to_start=True, message=str(exc)
        )
    return CommandResult(argv=tuple(argv), returncode=done.returncode)
