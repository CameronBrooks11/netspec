"""The Oracle protocol -- the only seam through which KiCad reaches this package.

Everything above this layer works on the canonical model and never learns that KiCad
exists. That boundary is the whole migration plan (DECISIONS D4): a future backend
speaking KiCad 11's IPC API implements this protocol and nothing above it changes.

The method names deliberately echo KiCad 11's own commands -- ``GetSchematicNetlist``,
``RunSchematicJobExport*``, DRC and ERC as jobs -- so that backend is a mapping rather
than a redesign.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from kicad_netspec.model import Netlist

__all__ = [
    "Capabilities",
    "EnvironmentError_",
    "Finding",
    "Oracle",
    "RuleReport",
]


class EnvironmentError_(RuntimeError):
    """The engine could not be run, or could not read its input.

    Distinct from any statement about the design (DECISIONS D10). A missing KiCad, a
    file that is not there, or a timeout is reported as ``verdict: error``, never as a
    board that failed its checks. CI on a machine without KiCad must not report a design
    as disproven.
    """


@dataclass(frozen=True)
class Capabilities:
    """What the active backend can actually do.

    Probed, never inferred from a version number (DECISIONS D4). Features degrade with a
    clear message instead of crashing against an unexpected KiCad build.
    """

    backend: str
    kicad_version: str
    netlist: bool = False
    erc: bool = False
    drc: bool = False
    schematic_parity: bool = False

    def __str__(self) -> str:
        have = [n for n in ("netlist", "erc", "drc", "schematic_parity") if getattr(self, n)]
        return f"{self.backend} (KiCad {self.kicad_version}): {', '.join(have) or 'nothing'}"


@dataclass(frozen=True)
class Finding:
    """One violation reported by ERC or DRC."""

    kind: str
    """KiCad's rule id, e.g. ``footprint_symbol_mismatch``."""

    severity: str
    """``error``, ``warning`` or ``exclusion``, as KiCad classified it.

    Reported verbatim so the caller decides what fails. netspec never lets KiCad's
    default severities decide silently -- see DECISIONS D13.
    """

    description: str = ""
    category: str = ""
    """Which sweep produced it: ``violations``, ``unconnected`` or ``parity``."""

    sheet: str = ""
    """Schematic sheet path, for ERC on a hierarchical design. Empty for board checks."""

    items: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"[{self.severity}] {self.kind}: {self.description}"


@dataclass(frozen=True)
class RuleReport:
    """The result of an ERC or DRC run, with severities preserved."""

    findings: tuple[Finding, ...] = ()
    source: str = ""
    kicad_version: str = ""
    severities_requested: tuple[str, ...] = field(default=("error", "warning", "exclusion"))

    def by_severity(self, severity: str) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == severity)

    @property
    def errors(self) -> tuple[Finding, ...]:
        return self.by_severity("error")

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return self.by_severity("warning")

    def __str__(self) -> str:
        return f"RuleReport({len(self.errors)} errors, {len(self.warnings)} warnings)"


@runtime_checkable
class Oracle(Protocol):
    """KiCad, asked politely.

    An implementation may shell out, talk over a socket, or anything else -- callers
    cannot tell, which is the point.
    """

    def capabilities(self) -> Capabilities:
        """What this backend can do, probed rather than assumed."""
        ...

    def netlist(self, schematic: Path, *, variant: str | None = None) -> Netlist:
        """KiCad's opinion of what is connected to what."""
        ...

    def erc(self, schematic: Path) -> RuleReport:
        """Electrical rules check, at every severity."""
        ...

    def drc(self, board: Path, *, schematic_parity: bool = True) -> RuleReport:
        """Design rules check, at every severity, including schematic parity."""
        ...
