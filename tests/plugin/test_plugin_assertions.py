"""The pytest helpers, tested without needing KiCad."""

from __future__ import annotations

import pytest

from kicad_netspec.contract import Spec, polarity
from kicad_netspec.model import Component, Net, Node, build_netlist
from kicad_netspec.pytest_plugin import (
    assert_net,
    assert_no_floating_pins,
    assert_polarity,
    assert_spec,
)


def _rail(plus_on: str = "VIN", minus_on: str = "GND", floating: bool = False):
    nets = {
        "VIN": [Node("R1", "1")],
        "GND": [Node("R1", "2")],
    }
    nets[plus_on].append(Node("C1", "1", "P_1"))
    nets[minus_on].append(Node("C1", "2"))
    built = [Net(name=k, nodes=frozenset(v)) for k, v in nets.items()]
    if floating:
        built.append(Net("unconnected-(J1-Pad1)", frozenset({Node("J1", "1")})))
    return build_netlist(built, [Component("C1"), Component("R1"), Component("J1")])


def test_assert_net_passes_and_fails_usefully() -> None:
    nl = _rail()
    assert_net(nl, "VIN", ["R1.1", "C1.1"])

    with pytest.raises(AssertionError, match="not a pin in this design"):
        assert_net(nl, "VIN", ["R1.1", "C1.1", "J1.1"])
    with pytest.raises(AssertionError, match="missing"):
        assert_net(_rail(floating=True), "VIN", ["R1.1", "C1.1", "J1.1"])
    with pytest.raises(AssertionError, match="unexpected"):
        assert_net(nl, "VIN", ["R1.1"])
    assert_net(nl, "VIN", ["R1.1"], exact=False)


def test_assert_net_names_the_alternatives_when_the_net_is_absent() -> None:
    with pytest.raises(AssertionError, match="Connected nets:"):
        assert_net(_rail(), "+5V", ["R1.1"])


def test_assert_net_resolves_a_pin_function() -> None:
    assert_net(_rail(), "VIN", ["R1.1", "C1.P"])


def test_assert_polarity_says_reversed_when_it_is() -> None:
    assert_polarity(_rail(), "C1", plus="VIN", minus="GND")
    with pytest.raises(AssertionError, match="IS REVERSED"):
        assert_polarity(_rail("GND", "VIN"), "C1", plus="VIN", minus="GND")


def test_assert_no_floating_pins_with_an_allowance() -> None:
    assert_no_floating_pins(_rail())
    with pytest.raises(AssertionError, match="floating"):
        assert_no_floating_pins(_rail(floating=True))
    assert_no_floating_pins(_rail(floating=True), allow=["J1.1"])


def test_assert_spec_reports_every_failure_at_once() -> None:
    spec = Spec(source="x", rules=[polarity("C1", plus="VIN", minus="GND")])
    assert_spec(spec, _rail())
    with pytest.raises(AssertionError, match="contract not satisfied"):
        assert_spec(spec, _rail("GND", "VIN"))
