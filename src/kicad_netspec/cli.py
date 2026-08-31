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

from kicad_netspec import __version__, snapshot
from kicad_netspec.diff import NetlistDiff, diff_netlists
from kicad_netspec.model import Netlist
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

    snap = subs.add_parser("snap", help="record connectivity as stable JSON")
    snap.add_argument("schematic", type=Path)
    snap.add_argument("-o", "--output", type=Path, required=True)
    snap.add_argument("--variant", default=None)
    snap.set_defaults(handler=_snap)

    d = subs.add_parser("diff", help="what changed between two readings of a design")
    d.add_argument("before", type=Path, help="a snapshot, or a schematic")
    d.add_argument("after", type=Path, help="a snapshot, or a schematic")
    d.add_argument("--variant", default=None)
    d.add_argument("--exit-zero", action="store_true", help="report changes but always exit 0")
    d.set_defaults(handler=_diff)

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


def _snap(args: argparse.Namespace) -> int:
    netlist = Cli10Backend().netlist(args.schematic, variant=args.variant)
    out = snapshot.write(netlist, args.output)
    print(f"{out}  ({len(netlist.components)} components, {len(netlist.nets)} nets)")
    return EXIT_OK


def _load_side(path: Path, variant: str | None) -> Netlist:
    """Accept either a snapshot or a design, so `diff` needs no flags to say which."""
    if path.suffix == ".json":
        try:
            return snapshot.read(path)
        except snapshot.SnapshotError as exc:
            raise EnvironmentError_(str(exc)) from exc
    return Cli10Backend().netlist(path, variant=variant)


def _diff(args: argparse.Namespace) -> int:
    before = _load_side(args.before, args.variant)
    after = _load_side(args.after, args.variant)
    result = diff_netlists(before, after)
    _print_diff(result)
    if args.exit_zero or result.empty:
        return EXIT_OK
    return EXIT_VIOLATION


def _print_diff(result: NetlistDiff) -> None:
    if result.empty and not result.benign:
        print("no change in connectivity")
        return

    if result.structural:
        print("NET CHANGES")
        for change in result.structural:
            print(f"  {change}")

    if result.component_changes:
        print("\nCOMPONENTS")
        for change in result.component_changes:
            print(f"  {change}")

    if result.now_floating or result.no_longer_floating:
        print("\nFLOATING PINS")
        for node in result.now_floating:
            print(f"  ! {node}  is no longer connected to anything")
        for node in result.no_longer_floating:
            print(f"  + {node}  is now connected")

    if result.pin_swaps:
        print("\nPIN SWAPS  (a connection moved between pins of one part)")
        for swap in result.pin_swaps:
            print(f"  {swap}")

    if result.benign:
        print(f"\n{len(result.benign)} net(s) renamed with no change in membership")

    print()
    if result.empty:
        print("no change in connectivity")
        return
    bits = []
    if result.structural:
        bits.append(f"{len(result.structural)} net change(s)")
    if result.component_changes:
        bits.append(f"{len(result.component_changes)} component change(s)")
    if result.now_floating:
        bits.append(f"{len(result.now_floating)} pin(s) newly floating")
    risky = [s for s in result.pin_swaps if s.polarity_risk]
    if result.pin_swaps:
        bits.append(f"{len(result.pin_swaps)} pin swap(s)")
    print("CHANGED  " + ", ".join(bits))
    if risky:
        print(
            f"\nWARNING: {len(risky)} pin swap(s) on a two-pin part. If any is polarised, "
            "it is now backwards, and ERC will not tell you."
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
