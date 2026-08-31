"""Snapshot, run something, re-read through KiCad, adjudicate.

The feature that makes netspec compose with the rest of the ecosystem instead of
competing with it. It does not care what did the editing -- an agent, a code generator,
a human with a mouse -- because it asks KiCad afterwards either way.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from kicad_netspec.check import CheckReport, check_spec
from kicad_netspec.contract import Spec
from kicad_netspec.diff import NetlistDiff, diff_netlists
from kicad_netspec.model import Netlist
from kicad_netspec.ops.run import CommandResult, run_command

__all__ = ["GuardResult", "guard"]


@dataclass(frozen=True)
class GuardResult:
    """What the wrapped command did to the design."""

    before: Netlist
    after: Netlist
    diff: NetlistDiff
    command: CommandResult
    check: CheckReport | None = None

    @property
    def ok(self) -> bool:
        """Green when the command succeeded and nothing asserted was violated.

        A change is not itself a failure -- editing the design is the point. What fails
        is a violated contract, or a change that carries the signature of a defect.
        """
        if not self.command.ok:
            return False
        if self.check is not None and self.check.verdict != "pass":
            return False
        return not self.diff.suspicious


def guard(
    schematic: Path,
    argv: Sequence[str],
    *,
    read: object,
    spec: Spec | None = None,
    cwd: str | None = None,
) -> GuardResult:
    """Read, run, re-read, compare.

    ``read`` is a callable taking a path and returning a :class:`Netlist` -- normally an
    oracle's ``netlist`` method. Injected so this stays free of any KiCad knowledge.
    """
    before = read(schematic)  # type: ignore[operator]
    command = run_command(argv, cwd=cwd)
    after = read(schematic)  # type: ignore[operator]
    return GuardResult(
        before=before,
        after=after,
        diff=diff_netlists(before, after),
        command=command,
        check=check_spec(spec, after) if spec is not None else None,
    )
