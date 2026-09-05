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


# -- net_of: the lookup every contract rule goes through --------------------------------


def _diode_netlist():
    """A part whose pins carry KiCad's function names, as Device:D does."""
    return build_netlist(
        [
            Net("VCC", frozenset({Node("D1", "1", function="A")})),
            Net("GND", frozenset({Node("D1", "2", function="K")})),
            Net("VIN", frozenset({Node("U1", "3", function="VI_3")})),
        ],
        [Component("D1", "1N4148"), Component("U1", "AMS1117")],
    )


def test_net_of_accepts_a_pin_number() -> None:
    assert _diode_netlist().net_of("D1", "1") == "VCC"


def test_net_of_accepts_a_pin_function_name() -> None:
    """The trap that reported correct boards as broken: net_of was number-only."""
    nl = _diode_netlist()
    assert nl.net_of("D1", "A") == "VCC"
    assert nl.net_of("D1", "K") == "GND"


def test_net_of_accepts_kicads_suffixed_function_form() -> None:
    nl = _diode_netlist()
    assert nl.net_of("U1", "VI") == "VIN"
    assert nl.net_of("U1", "VI_3") == "VIN"


def test_net_of_is_none_for_a_pin_that_does_not_exist() -> None:
    nl = _diode_netlist()
    assert nl.net_of("D1", "9") is None
    assert nl.net_of("NOSUCH", "1") is None


def test_nodes_of_returns_a_components_pins_in_order() -> None:
    nl = _diode_netlist()
    assert [n.pin for n in nl.nodes_of("D1")] == ["1", "2"]
    assert nl.nodes_of("NOSUCH") == ()


# -- the partition invariant ------------------------------------------------------------


def test_a_pin_cannot_belong_to_two_nets() -> None:
    """A netlist partitions pins into nets. Anything else is malformed, not a short.

    KiCad merges shorted nets and keeps one name, so it never emits this -- verified
    across every fixture, a deliberate dead short, and a real 135-node board. Accepting
    it silently meant one of the two nets won by insertion order.
    """
    with pytest.raises(ValueError) as caught:
        build_netlist(
            [
                Net("VIN", frozenset({Node("R1", "1")})),
                Net("GND", frozenset({Node("R1", "1")})),
            ],
            [Component("R1", "1k")],
        )
    message = str(caught.value)
    assert "R1.1" in message
    assert "VIN" in message and "GND" in message


def test_a_malformed_netlist_reaches_the_oracle_as_a_parse_error() -> None:
    """The model's invariant has to surface as "I could not read this", not a crash.

    ``Cli10Backend`` catches ``ParseError`` and reports an environment fault; a bare
    ``ValueError`` would escape as a traceback and read like a crash in netspec.
    """
    from kicad_netspec.parse import ParseError, parse_kicadxml

    doc = """<export version="E">
      <components><comp ref="R1"><value>1k</value></comp></components>
      <nets>
        <net code="1" name="VIN"><node ref="R1" pin="1"/></net>
        <net code="2" name="GND"><node ref="R1" pin="1"/></net>
      </nets>
    </export>"""

    with pytest.raises(ParseError) as caught:
        parse_kicadxml(doc)
    assert "R1.1" in str(caught.value)
