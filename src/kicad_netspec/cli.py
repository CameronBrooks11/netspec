"""Command line entry point.

Argparse, not click or typer: the core has no runtime dependencies and this is the
reason to keep it that way.

Exit codes are part of the contract (DECISIONS D10) and mean different things::

    0  the design was read, and nothing asked for was violated
    1  the design was read, and something was violated -- a statement about the design
    4  the engine could not run, or its input was unreadable -- NOT about the design
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from kicad_netspec import __version__
from kicad_netspec.oracle import Cli10Backend, EnvironmentError_, find_kicad_cli

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 4


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(args.handler(args))
    except EnvironmentError_ as exc:
        # An environment fault is never a statement about the design.
        print(f"error: {exc}", file=sys.stderr)
        print(
            "\nThis is an environment problem, not a finding about your design.",
            file=sys.stderr,
        )
        return EXIT_ENVIRONMENT


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netspec",
        description="Verify PCB connectivity against declared engineering intent.",
    )
    parser.add_argument("--version", action="version", version=f"netspec {__version__}")
    subs = parser.add_subparsers(dest="command")

    doctor = subs.add_parser("doctor", help="check that a KiCad engine can be found and run")
    doctor.set_defaults(handler=_doctor)

    netlist = subs.add_parser("netlist", help="show what KiCad thinks is connected")
    netlist.add_argument("schematic", type=Path)
    netlist.add_argument("--variant", default=None, help="design variant to export")
    netlist.add_argument(
        "--all", action="store_true", help="include nets with only one pin on them"
    )
    netlist.set_defaults(handler=_netlist)

    return parser


def _doctor(_args: argparse.Namespace) -> int:
    cli = find_kicad_cli()
    caps = Cli10Backend(cli).capabilities()
    print(f"engine   : {cli}")
    print(f"invoked  : {' '.join(cli.argv)}")
    if cli.sandboxed:
        print("note     : sandboxed engine; work files are written beside the design")
    print(f"can do   : {caps}")
    return EXIT_OK


def _netlist(args: argparse.Namespace) -> int:
    netlist = Cli10Backend().netlist(args.schematic, variant=args.variant)

    print(f"{netlist.source or args.schematic}")
    print(f"  {len(netlist.components)} components, {len(netlist.nets)} nets")

    shown = netlist.nets.values() if args.all else netlist.connected_nets
    width = max((len(n.name) for n in shown), default=0)
    for net in sorted(shown, key=lambda n: (n.anonymous, n.name)):
        pins = ", ".join(sorted(str(node) for node in net.nodes))
        marker = " " if net.connected else "!"
        print(f"  {marker} {net.name:<{width}}  {pins}")

    isolated = netlist.isolated_nodes
    if isolated and not args.all:
        print(f"\n  {len(isolated)} pin(s) connected to nothing: ", end="")
        print(", ".join(str(n) for n in isolated[:8]) + (" ..." if len(isolated) > 8 else ""))
    if not netlist.connected_nets:
        print("\n  nothing in this schematic is connected to anything.")
        return EXIT_VIOLATION
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
