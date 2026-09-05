"""pytest integration: assert connectivity from a test suite.

Registered as a pytest11 entry point, so installing the package is enough::

    def test_the_rail_is_wired(netlist):
        nl = netlist("hardware/board.kicad_sch")
        assert_net(nl, "VIN", ["J1.1", "C1.1", "U1.VI"])
        assert_polarity(nl, "C1", plus="VIN", minus="GND")

The oracle is created once per session, because finding and probing KiCad costs more
than any single call. When there is no KiCad, every test that asks for one is **skipped**
rather than failed -- a machine without the engine has not disproved anyone's board
(DECISIONS D10).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from kicad_netspec.check import check_spec, net_of_pin
from kicad_netspec.contract import Spec
from kicad_netspec.model import Netlist
from kicad_netspec.oracle import Cli10Backend, EnvironmentError_, KiCadNotFound

__all__ = [
    "assert_net",
    "assert_no_floating_pins",
    "assert_polarity",
    "assert_spec",
]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "needs_kicad: test requires a working KiCad engine; skipped without one"
    )


@pytest.fixture(scope="session")
def kicad() -> Cli10Backend:
    """A KiCad oracle, or a skip if this machine has none."""
    try:
        return Cli10Backend()
    except (KiCadNotFound, EnvironmentError_) as exc:
        pytest.skip(f"no KiCad available: {exc}")


@pytest.fixture(scope="session")
def netlist(kicad: Cli10Backend) -> Callable[[str | Path], Netlist]:
    """Read a schematic through KiCad. Results are cached per session."""
    cache: dict[str, Netlist] = {}

    def read(path: str | Path) -> Netlist:
        key = str(Path(path).resolve())
        if key not in cache:
            cache[key] = kicad.netlist(Path(path))
        return cache[key]

    return read


# -- assertions, written to fail with a message you can act on -------------------------


def assert_net(netlist: Netlist, name: str, pins: Iterable[str], *, exact: bool = True) -> None:
    """Assert exactly which pins are on a net.

    Pins may be given by number (``C1.1``) or by function (``U1.VI``); prefer the
    function, since pin numbers are what the known schematic-writer bugs corrupt.
    """
    net = netlist.nets.get(name)
    if net is None:
        available = ", ".join(sorted(n.name for n in netlist.connected_nets)[:12])
        raise AssertionError(f"no net called {name!r}. Connected nets: {available}")

    wanted = set()
    for pin in pins:
        node = netlist.resolve(pin)
        if node is None:
            raise AssertionError(f"{pin} is not a pin in this design")
        wanted.add(str(node))

    actual = {str(n) for n in net.nodes}
    missing, extra = sorted(wanted - actual), sorted(actual - wanted)
    problems = []
    if missing:
        problems.append(f"missing {', '.join(missing)}")
    if extra and exact:
        problems.append(f"unexpected {', '.join(extra)}")
    if problems:
        raise AssertionError(f"net {name}: " + "; ".join(problems))


def assert_polarity(
    netlist: Netlist,
    ref: str,
    *,
    plus: str,
    minus: str,
    plus_pin: str = "1",
    minus_pin: str = "2",
) -> None:
    """Assert a polarised part is the right way round.

    ERC has no rule for this, because reversing a polarised part is legal wiring.
    """
    if ref not in netlist.components:
        raise AssertionError(f"{ref} is not in this design")

    got_plus = net_of_pin(netlist, ref, plus_pin)
    got_minus = net_of_pin(netlist, ref, minus_pin)
    if got_plus == plus and got_minus == minus:
        return

    detail = f"{ref} pin {plus_pin} is on {got_plus or 'nothing'} (expected {plus}), "
    detail += f"pin {minus_pin} is on {got_minus or 'nothing'} (expected {minus})"
    if got_plus == minus and got_minus == plus:
        detail += f" -- {ref} IS REVERSED"
    raise AssertionError(detail)


def assert_no_floating_pins(netlist: Netlist, *, allow: Iterable[str] = ()) -> None:
    """Assert every pin is connected, apart from ones you name."""
    permitted = {str(netlist.resolve(a) or a) for a in allow}
    floating = [str(n) for n in netlist.isolated_nodes if str(n) not in permitted]
    if floating:
        shown = ", ".join(floating[:12])
        more = f" (and {len(floating) - 12} more)" if len(floating) > 12 else ""
        raise AssertionError(f"{len(floating)} floating pin(s): {shown}{more}")


def assert_spec(spec: Spec, netlist: Netlist) -> None:
    """Assert every rule in a contract, reporting all failures at once."""
    report = check_spec(spec, netlist)
    if report.verdict == "pass":
        return
    lines = [str(r) for r in report.results if not r.green]
    raise AssertionError("contract not satisfied:\n  " + "\n  ".join(lines))
