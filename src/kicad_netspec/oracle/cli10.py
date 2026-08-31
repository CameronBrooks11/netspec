"""KiCad 8/9/10 backend: shell out to ``kicad-cli``.

Every capability here exists in shipping KiCad and has been measured (DECISIONS D5).
Netlist export runs in well under a second even on a hierarchical board, which is what
makes it affordable to re-read after every single edit; ERC and DRC take seconds, so
they belong at a checkpoint rather than in a tight loop.

Two behaviours are load-bearing and easy to get wrong:

* **Severity.** Rule checks always run at ``--severity-all``. At KiCad's defaults,
  ``pcb drc --schematic-parity`` reports zero problems on a board carrying 147 of them,
  because ``footprint_symbol_mismatch`` defaults to ``warning``. See DECISIONS D13.
* **Sandboxes.** A Flatpak KiCad has its own ``/tmp``, so an output path there is
  written somewhere the caller cannot read. Work files go beside the input instead.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from kicad_netspec.model import Netlist
from kicad_netspec.oracle.base import Capabilities, EnvironmentError_, Finding, RuleReport
from kicad_netspec.oracle.discovery import KiCadCli, KiCadNotFound, find_kicad_cli
from kicad_netspec.parse import ParseError, parse_kicadxml_file

__all__ = ["Cli10Backend"]

_TIMEOUT = 300
_ALL_SEVERITIES = ("error", "warning", "exclusion")


class Cli10Backend:
    """An :class:`~kicad_netspec.oracle.base.Oracle` backed by ``kicad-cli``."""

    def __init__(self, cli: KiCadCli | None = None) -> None:
        try:
            self._cli = cli or find_kicad_cli()
        except KiCadNotFound as exc:
            raise EnvironmentError_(str(exc)) from exc

    @property
    def cli(self) -> KiCadCli:
        return self._cli

    # -- protocol --------------------------------------------------------------

    def capabilities(self) -> Capabilities:
        return Capabilities(
            backend="kicad-cli",
            kicad_version=self._cli.version,
            netlist=True,
            erc=True,
            drc=True,
            schematic_parity=True,
        )

    def netlist(self, schematic: Path, *, variant: str | None = None) -> Netlist:
        source = _require(schematic)
        args = ["sch", "export", "netlist", "--format", "kicadxml"]
        if variant:
            args += ["--variant", variant]
        with self._workfile(source, ".netlist.xml") as out:
            self._run([*args, "--output", str(out), str(source)])
            if not out.exists():
                raise EnvironmentError_(
                    f"kicad-cli reported success but wrote no netlist for {source}"
                )
            try:
                parsed = parse_kicadxml_file(out)
            except ParseError as exc:
                raise EnvironmentError_(f"could not read the netlist KiCad wrote: {exc}") from exc
        # Report the design as the source, not the throwaway file KiCad wrote for us.
        return dataclasses.replace(parsed, source=str(source))

    def erc(self, schematic: Path) -> RuleReport:
        source = _require(schematic)
        with self._workfile(source, ".erc.json") as out:
            self._run(
                [
                    "sch",
                    "erc",
                    "--format",
                    "json",
                    "--severity-all",
                    "--output",
                    str(out),
                    str(source),
                ]
            )
            payload = _load(out, source)
        return _report(payload, source, ("violations",))

    def drc(self, board: Path, *, schematic_parity: bool = True) -> RuleReport:
        source = _require(board)
        args = ["pcb", "drc", "--format", "json", "--severity-all"]
        if schematic_parity:
            args.append("--schematic-parity")
        with self._workfile(source, ".drc.json") as out:
            self._run([*args, "--output", str(out), str(source)])
            payload = _load(out, source)
        return _report(payload, source, ("violations", "unconnected_items", "schematic_parity"))

    # -- plumbing --------------------------------------------------------------

    def _run(self, args: Sequence[str]) -> str:
        argv = [*self._cli.argv, *args]
        try:
            done = subprocess.run(  # noqa: S603 - argv built here, never from user text
                argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise EnvironmentError_(f"kicad-cli timed out after {_TIMEOUT}s") from exc
        except OSError as exc:
            raise EnvironmentError_(f"could not run kicad-cli: {exc}") from exc

        # kicad-cli uses non-zero exits to signal *findings* as well as failure, so the
        # return code alone cannot distinguish "board has violations" from "engine
        # broke". The callers here always read a written artifact, and treat a missing
        # artifact as the real failure.
        if done.returncode != 0 and not done.stdout and done.stderr:
            first = done.stderr.strip().splitlines()
            if first and "violation" not in first[0].lower():
                raise EnvironmentError_(f"kicad-cli failed: {first[0]}")
        return done.stdout

    def _workfile(self, near: Path, suffix: str):
        """A temporary file the engine can definitely write to.

        Placed beside the design rather than in ``/tmp``: a sandboxed KiCad has its own
        ``/tmp`` and would write out of sight.
        """
        directory = near.parent if self._cli.sandboxed else None
        return _TempFile(suffix=suffix, directory=directory)


class _TempFile:
    """Context manager yielding a path that is removed on exit."""

    def __init__(self, *, suffix: str, directory: Path | None) -> None:
        self._suffix = suffix
        self._dir = directory
        self._path: Path | None = None

    def __enter__(self) -> Path:
        handle, name = tempfile.mkstemp(
            prefix="netspec-", suffix=self._suffix, dir=str(self._dir) if self._dir else None
        )
        import os

        os.close(handle)
        self._path = Path(name)
        # kicad-cli refuses to overwrite in some modes; hand it a name, not a file.
        self._path.unlink(missing_ok=True)
        return self._path

    def __exit__(self, *exc: object) -> None:
        if self._path is not None:
            self._path.unlink(missing_ok=True)


def _require(path: Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise EnvironmentError_(f"no such file: {path}")
    return resolved


def _load(path: Path, source: Path) -> dict:
    if not path.exists():
        raise EnvironmentError_(f"kicad-cli wrote no report for {source}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentError_(f"could not read the report KiCad wrote: {exc}") from exc


def _report(payload: dict, source: Path, categories: Sequence[str]) -> RuleReport:
    """Normalise KiCad's two report shapes into one.

    ``pcb drc`` puts findings in flat top-level lists. ``sch erc`` nests them per sheet
    under ``sheets[].violations``, which is easy to miss: reading only the top level
    yields an empty report on a schematic that in fact has violations.
    """
    findings: list[Finding] = []

    for category in categories:
        for entry in payload.get(category, ()):
            findings.append(_finding(entry, category))

    for sheet in payload.get("sheets", ()):
        path = sheet.get("path", "")
        for entry in sheet.get("violations", ()):
            findings.append(_finding(entry, "violations", sheet=path))

    return RuleReport(
        findings=tuple(findings),
        source=str(source),
        kicad_version=str(payload.get("kicad_version", "")),
        severities_requested=_ALL_SEVERITIES,
    )


def _finding(entry: dict, category: str, *, sheet: str = "") -> Finding:
    return Finding(
        kind=entry.get("type", "unknown"),
        severity=entry.get("severity", "unknown"),
        description=entry.get("description", ""),
        category=category,
        sheet=sheet,
        items=tuple(
            i.get("description", "") for i in entry.get("items", ()) if isinstance(i, dict)
        ),
    )
