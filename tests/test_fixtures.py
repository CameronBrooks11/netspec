"""Regression tests built from bugs that shipped.

Each fixture is a real schematic produced by a real tool, paired with the netlist KiCad
itself derived from it (``*.expected.xml``). Together they are the argument for this
project existing: in every case the file is valid, opens cleanly, and is wrong in a way
no editor reported.

These run without KiCad, against the recorded ground truth. ``test_oracle.py`` re-derives
the same facts from a live engine when one is available.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_netspec.model import Netlist
from kicad_netspec.parse import parse_kicadxml_file

FIXTURES = Path(__file__).parent / "fixtures"


def _netlist(name: str) -> Netlist:
    return parse_kicadxml_file(FIXTURES / f"{name}.expected.xml")


# --------------------------------------------------------------------------------------
# The known-good baseline. Anything that fails here is our bug, not the ecosystem's.
# --------------------------------------------------------------------------------------


def test_good_ldo_is_wired_as_intended() -> None:
    """A code-first (SKiDL) LDO: every net is real and carries the intended pins."""
    nl = _netlist("good_ldo")

    assert len(nl.connected_nets) == 3
    assert not nl.isolated_nodes, "a correct schematic leaves no pin dangling"

    assert {str(n) for n in nl.nets["VIN"].nodes} == {"C1.1", "U1.3"}
    assert {str(n) for n in nl.nets["+3V3"].nodes} == {"C2.1", "U1.2"}
    assert {str(n) for n in nl.nets["GND"].nodes} == {"C1.2", "C2.2", "U1.1"}


def test_good_ldo_exposes_pin_functions() -> None:
    """``pinfunction`` is what lets a contract say ``U1.VI`` instead of ``U1.3``."""
    nl = _netlist("good_ldo")
    assert str(nl.resolve("U1.VI")) == "U1.3"
    assert str(nl.resolve("U1.VO")) == "U1.2"
    assert str(nl.resolve("U1.GND")) == "U1.1"


# --------------------------------------------------------------------------------------
# Failure 1 -- wires that connect nothing, reported as success.
# --------------------------------------------------------------------------------------


def test_dangling_wires_produce_no_connectivity() -> None:
    """Three parts, three wires, every call succeeded -- and nothing is connected.

    Wires were placed at coordinates that look right. KiCad joins a wire to a pin only
    on exact coincidence, so the schematic opens fine and is electrically empty. This is
    the failure a netlist check catches and an eyeball does not.
    """
    nl = _netlist("dangling_wires")

    assert len(nl.nets) == 7, "KiCad reports one net per isolated pin"
    assert nl.connected_nets == (), "not one net joins two pins"
    assert len(nl.isolated_nodes) == 7

    # Every net is anonymous, because KiCad had nothing but the pin to name it after.
    assert all(net.anonymous for net in nl.nets.values())


def test_dangling_wires_differ_from_the_good_board() -> None:
    """The same circuit, drawn two ways: one works, one is inert."""
    good, bad = _netlist("good_ldo"), _netlist("dangling_wires")
    assert {c for c in good.components} == {c for c in bad.components}, "same parts"
    assert len(good.connected_nets) == 3
    assert len(bad.connected_nets) == 0


# --------------------------------------------------------------------------------------
# Failure 2 -- a sign error swaps pin 1 and pin 2, silently.
# --------------------------------------------------------------------------------------


def test_swapped_pins_wires_the_pin_that_was_not_asked_for() -> None:
    """``C1.1`` was requested; KiCad reports ``C1.2`` on the wire.

    A schematic-writing helper added a symbol-relative pin offset instead of
    subtracting it, so the library's y-up frame was never flipped into the schematic's
    y-down frame. For a vertically placed two-pin part that exchanges the pins.

    Connectivity exists here, so a "did I get nets?" check passes. Only comparing
    against intent catches it.
    """
    nl = _netlist("swapped_pins")

    connected = nl.connected_nets
    assert len(connected) == 1, "one wire was drawn, so one real net"

    pins = {str(n) for n in connected[0].nodes}
    assert "R1.1" in pins
    assert "C1.2" in pins, "the wrong pin of C1 was wired"
    assert "C1.1" not in pins, "the pin actually asked for is not on the net"


# --------------------------------------------------------------------------------------
# Failure 3 -- the same bug on a polarised part, which ERC will not flag.
# --------------------------------------------------------------------------------------


def test_reversed_polarized_cap_is_backwards_and_erc_cannot_help() -> None:
    """The rail was wired to the negative terminal of an electrolytic capacitor.

    ``Device:C_Polarized`` pin 1 is ``+`` and pin 2 is ``-``. The contract asked for
    pin 1. The netlist has pin 2.

    Reversing a polarised capacitor is *legal wiring*: ERC has no rule against it, the
    file is valid, and the netlist is populated. Nothing short of comparing against
    declared intent distinguishes this from a correct board -- which is the whole reason
    netspec exists.
    """
    nl = _netlist("reversed_polarized_cap")

    connected = nl.connected_nets
    assert len(connected) == 1

    pins = {str(n) for n in connected[0].nodes}
    assert pins == {"C1.2", "R1.1"}, "the negative terminal is the one that got wired"
    assert "C1.1" not in pins, "the positive terminal -- what was asked for -- is floating"

    # And the give-away that no automated rule fires on: the part is still fully
    # described, still placed, still has both pins present in the design.
    assert len(nl.nodes_of("C1")) == 2


@pytest.mark.parametrize(
    "fixture,connected",
    [
        ("good_ldo", 3),
        ("dangling_wires", 0),
        ("swapped_pins", 1),
        ("reversed_polarized_cap", 1),
        ("hierarchy", 1),
    ],
)
def test_fixture_ground_truth_is_stable(fixture: str, connected: int) -> None:
    """Guards the fixtures themselves against silent drift."""
    assert len(_netlist(fixture).connected_nets) == connected


# --------------------------------------------------------------------------------------
# The diff, on real files. This pair is the argument for the project in one command.
# --------------------------------------------------------------------------------------


def test_diffing_a_correct_board_against_the_reversed_one_names_the_defect() -> None:
    """Two schematics that ERC cannot tell apart, and the exact difference between them.

    ``polarized_cap_correct`` wires the rail to C1 pin 1 (+).
    ``reversed_polarized_cap`` wires it to C1 pin 2 (-).

    KiCad's ERC reports the same two violations for both, because reversing a polarised
    capacitor is legal wiring. The netlist diff names it.
    """
    from kicad_netspec.diff import diff_netlists

    result = diff_netlists(_netlist("polarized_cap_correct"), _netlist("reversed_polarized_cap"))

    (swap,) = result.pin_swaps
    assert swap.ref == "C1"
    assert (swap.was.pin, swap.now.pin) == ("1", "2")
    assert swap.polarity_risk

    assert [str(n) for n in result.now_floating] == ["C1.1"], "the + terminal came free"
    assert len(result.structural) == 1, "one edit reads as one change"


def test_a_board_falling_apart_reads_as_floating_pins_not_net_churn() -> None:
    """Losing every connection is 7 floating pins, not 3 removals and 7 additions."""
    from kicad_netspec.diff import diff_netlists

    result = diff_netlists(_netlist("good_ldo"), _netlist("dangling_wires"))
    assert len(result.now_floating) == 7
    assert len(result.structural) == 3, "the three real nets went away"
    assert not result.pin_swaps


def test_a_netlist_does_not_differ_from_itself() -> None:
    from kicad_netspec.diff import diff_netlists

    for name in ("good_ldo", "dangling_wires", "swapped_pins", "reversed_polarized_cap"):
        assert diff_netlists(_netlist(name), _netlist(name)).empty


# --------------------------------------------------------------------------------------
# The polarity rule, on real boards that differ only in one component's orientation.
# --------------------------------------------------------------------------------------


def test_a_contract_catches_the_reversed_capacitor_on_a_real_board() -> None:
    """Both boards were generated by SKiDL, which wires pins correctly.

    The only difference is that one deliberately puts C1's minus terminal on VIN. KiCad's
    ERC reports 1 error and 3 warnings for *both*, because reversing a polarised
    capacitor is legal wiring. The contract distinguishes them.
    """
    from kicad_netspec.check import check_spec
    from kicad_netspec.contract import Spec, net, polarity

    rules = [
        net("VIN", ["J1.1", "C1.1", "R1.1"]),
        net("GND", ["J1.2", "C1.2", "R1.2"]),
        polarity("C1", plus="VIN", minus="GND"),
    ]
    spec = Spec(source="unused", rules=tuple(rules))

    good = check_spec(spec, _netlist("polcap_rail_correct"))
    assert good.verdict == "pass"

    bad = check_spec(spec, _netlist("polcap_rail_reversed"))
    assert bad.verdict == "fail"
    detail = " ".join(r.detail for r in bad.failures)
    assert "REVERSED" in detail


def test_the_two_rail_boards_differ_only_in_that_one_component() -> None:
    from kicad_netspec.diff import diff_netlists

    result = diff_netlists(_netlist("polcap_rail_correct"), _netlist("polcap_rail_reversed"))
    assert {s.ref for s in result.pin_swaps} == {"C1"}
    assert all(s.polarity_risk for s in result.pin_swaps)
    assert not result.component_changes, "same parts, same values"
    assert not result.now_floating, "nothing came loose; it was simply turned around"


# --------------------------------------------------------------------------------------
# Hierarchy -- the shape every real multi-sheet board has, and none of the above do.
# --------------------------------------------------------------------------------------


def test_hierarchy_fixture_carries_all_three_resolution_outcomes() -> None:
    """Hand-built, because no code-first tool in the survey emits hierarchical sheets.

    KiCad derived the netlist, so the *names* are its own. The design exists to hold one
    of each thing a contract's net name can turn out to be.
    """
    nl = _netlist("hierarchy")

    assert set(nl.nets) == {
        "VIN",
        "/Aux/IN",
        "/Aux/SENSE",
        "/Channel1/OUT",
        "/Channel2/OUT",
    }
    assert {str(n) for n in nl.nets["VIN"].nodes} == {"R1.1", "R2.1"}
