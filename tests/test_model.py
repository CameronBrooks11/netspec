"""Unit tests for the canonical model -- no KiCad needed."""

from __future__ import annotations

import pytest

from kicad_netspec.model import Component, Net, Node, build_netlist, parse_pin_ref


def _net(name: str, *pins: tuple[str, str]) -> Net:
    return Net(name=name, nodes=frozenset(Node(ref=r, pin=p) for r, p in pins))


def test_anonymous_nets_are_recognised() -> None:
    assert _net("Net-(C1-Pad1)", ("C1", "1")).anonymous
    assert _net("unconnected-(U1-VI-Pad3)", ("U1", "3")).anonymous
    assert not _net("VIN", ("C1", "1")).anonymous
    assert not _net("+3V3", ("C1", "1")).anonymous


def test_a_single_pin_net_is_not_connected() -> None:
    assert not _net("VIN", ("C1", "1")).connected
    assert _net("VIN", ("C1", "1"), ("U1", "3")).connected


def test_position_carries_no_meaning() -> None:
    """Two netlists with the same connectivity are equal, however they were drawn."""
    a = build_netlist([_net("VIN", ("C1", "1"), ("U1", "3"))], [Component("C1")])
    b = build_netlist([_net("VIN", ("U1", "3"), ("C1", "1"))], [Component("C1")])
    assert a.nets == b.nets


def test_resolve_prefers_pin_number_then_function() -> None:
    net = Net(
        name="VIN",
        nodes=frozenset(
            {
                Node(ref="U1", pin="3", function="VI_3", type="power_in"),
                Node(ref="C1", pin="1"),
            }
        ),
    )
    nl = build_netlist([net], [])
    assert nl.resolve("U1.3") is not None
    assert nl.resolve("U1.VI") == nl.resolve("U1.3"), "pin function must resolve"
    assert nl.resolve("U1.VI_3") == nl.resolve("U1.3"), "KiCad's suffixed form must resolve"
    assert nl.resolve("U1.vi") == nl.resolve("U1.3"), "matching is case-insensitive"
    assert nl.resolve("U1.NOPE") is None
    assert nl.resolve("nonsense") is None


def test_net_of_indexes_every_pin() -> None:
    nl = build_netlist([_net("VIN", ("C1", "1"), ("U1", "3"))], [])
    assert nl.net_of("C1", "1") == "VIN"
    assert nl.net_of("C1", "2") is None


@pytest.mark.parametrize("bad", ["C1", "", ".1", "C1."])
def test_parse_pin_ref_refuses_to_guess(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_pin_ref(bad)
