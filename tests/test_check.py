"""Contract adjudication. Netlists built directly, so no KiCad needed."""

from __future__ import annotations

import pytest

from kicad_netspec.check import check_spec
from kicad_netspec.contract import Spec, forbid, net, polarity
from kicad_netspec.model import Component, Net, Node, build_netlist


def _rail(c1_plus_on: str = "VIN", c1_minus_on: str = "GND"):
    """A cap across a rail; which pin sits on which net is the variable."""
    nets = {"VIN": [Node("R1", "1")], "GND": [Node("R1", "2")]}
    nets[c1_plus_on].append(Node("C1", "1"))
    nets[c1_minus_on].append(Node("C1", "2"))
    return build_netlist(
        [Net(name=k, nodes=frozenset(v)) for k, v in nets.items()],
        [Component("C1", "100uF"), Component("R1", "1k")],
    )


def _status(spec: Spec, netlist) -> list[str]:
    return [r.status for r in check_spec(spec, netlist).results]


# -- polarity: the rule that exists because ERC has none ------------------------------


def test_polarity_passes_when_the_part_is_the_right_way_round() -> None:
    spec = Spec(source="x", rules=[polarity("C1", plus="VIN", minus="GND")])
    report = check_spec(spec, _rail())
    assert report.verdict == "pass"


def test_polarity_fails_and_says_so_plainly_when_reversed() -> None:
    spec = Spec(source="x", rules=[polarity("C1", plus="VIN", minus="GND")])
    report = check_spec(spec, _rail(c1_plus_on="GND", c1_minus_on="VIN"))

    assert report.verdict == "fail"
    (result,) = report.failures
    assert "REVERSED" in result.detail
    assert "ERC does not check this" in result.detail


def test_polarity_on_an_absent_part_is_skipped_not_passed() -> None:
    """A rule about a part that is not there has not been satisfied (D9)."""
    spec = Spec(source="x", rules=[polarity("C9", plus="VIN", minus="GND")])
    report = check_spec(spec, _rail())
    assert _status(spec, _rail()) == ["skipped"]
    assert report.verdict == "fail", "skipped is not green"


# -- nets ------------------------------------------------------------------------------


def test_exact_net_rejects_an_unexpected_pin() -> None:
    netlist = _rail()
    assert _status(Spec(source="x", rules=[net("VIN", ["R1.1"])]), netlist) == ["fail"]
    assert _status(Spec(source="x", rules=[net("VIN", ["R1.1", "C1.1"])]), netlist) == ["pass"]


def test_non_exact_net_allows_extras() -> None:
    spec = Spec(source="x", rules=[net("VIN", ["R1.1"], exact=False)])
    assert _status(spec, _rail()) == ["pass"]


def test_a_missing_net_fails_rather_than_skipping() -> None:
    """A net the contract names that does not exist is a real failure, not an excuse."""
    spec = Spec(source="x", rules=[net("+5V", ["R1.1"])])
    report = check_spec(spec, _rail())
    assert report.results[0].status == "fail"
    assert "no net called" in report.results[0].detail


def test_a_pin_that_does_not_exist_is_skipped() -> None:
    spec = Spec(source="x", rules=[net("VIN", ["R1.1", "C9.1"])])
    assert _status(spec, _rail()) == ["skipped"]


# -- forbid ----------------------------------------------------------------------------


def test_forbid_passes_when_the_rails_are_separate() -> None:
    spec = Spec(source="x", rules=[forbid("VIN", "GND")])
    assert _status(spec, _rail()) == ["pass"]


def test_forbid_catches_a_pin_on_both_rails() -> None:
    shorted = build_netlist(
        [
            Net("VIN", frozenset({Node("R1", "1"), Node("C1", "1")})),
            Net("GND", frozenset({Node("R1", "1"), Node("C1", "2")})),
        ],
        [Component("C1"), Component("R1")],
    )
    spec = Spec(source="x", rules=[forbid("VIN", "GND")])
    report = check_spec(spec, shorted)
    assert report.verdict == "fail"
    assert "R1.1 is on both" in report.failures[0].detail


# -- the spec object itself ------------------------------------------------------------


def test_a_spec_needs_a_source() -> None:
    with pytest.raises(ValueError):
        Spec(source="")


def test_a_spec_rejects_a_non_rule() -> None:
    with pytest.raises(TypeError):
        Spec(source="x", rules=("not a rule",))  # type: ignore[arg-type]


def test_forbid_needs_two_nets() -> None:
    with pytest.raises(ValueError):
        forbid("VIN")


def test_require_no_floating_pins_is_opt_in() -> None:
    floating = build_netlist(
        [Net("unconnected-(C1-Pad1)", frozenset({Node("C1", "1")}))], [Component("C1")]
    )
    assert check_spec(Spec(source="x"), floating).verdict == "pass"
    strict = Spec(source="x", require_no_floating_pins=True)
    assert check_spec(strict, floating).verdict == "fail"
