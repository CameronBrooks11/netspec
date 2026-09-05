"""Matching the names a contract uses to the design it adjudicates.

Every case here runs against ``hierarchy.expected.xml`` -- a real netlist, produced by
KiCad from a real hierarchical schematic, chosen because it contains all three outcomes
at once::

    VIN            a root-level net, matched exactly
    /Aux/SENSE     matched by its leaf, uniquely
    /Channel*/OUT  matched by its leaf, twice, with no correct guess
"""

from __future__ import annotations

from pathlib import Path

from kicad_netspec.check import check_spec
from kicad_netspec.contract import Spec, forbid, net
from kicad_netspec.parse import parse_kicadxml_file
from kicad_netspec.resolve import find_nets, resolve_spec

FIXTURES = Path(__file__).parent / "fixtures"


def _hier():
    return parse_kicadxml_file(FIXTURES / "hierarchy.expected.xml")


# -- finding a net by the name a contract used -----------------------------------------


def test_an_exact_name_matches_outright() -> None:
    assert find_nets(_hier(), "VIN") == ("VIN",)


def test_a_leaf_name_finds_the_hierarchical_net() -> None:
    """The headline: a contract may spell the leaf, not the whole path."""
    assert find_nets(_hier(), "SENSE") == ("/Aux/SENSE",)


def test_a_full_path_still_matches_exactly() -> None:
    assert find_nets(_hier(), "/Channel1/OUT") == ("/Channel1/OUT",)


def test_a_leaf_shared_by_two_sheets_returns_both() -> None:
    """No correct guess exists, so resolution must not make one."""
    assert find_nets(_hier(), "OUT") == ("/Channel1/OUT", "/Channel2/OUT")


def test_a_name_that_is_nowhere_matches_nothing() -> None:
    assert find_nets(_hier(), "NOPE") == ()


# -- resolution as a phase over a whole contract ---------------------------------------


def test_resolution_reports_the_canonical_name() -> None:
    spec = Spec(source="x", rules=[net("SENSE", ["R3.2"])])
    resolved = resolve_spec(spec, _hier())
    assert resolved.net("SENSE") == "/Aux/SENSE"


def test_resolution_flags_an_ambiguous_name_without_choosing() -> None:
    spec = Spec(source="x", rules=[net("OUT", ["R1.2"])])
    resolved = resolve_spec(spec, _hier())
    outcome = resolved.by_name["OUT"]
    assert outcome.ambiguous
    assert outcome.target is None, "resolution must not pick a channel for the author"
    assert outcome.candidates == ("/Channel1/OUT", "/Channel2/OUT")


# -- what a contract author actually sees ----------------------------------------------


def test_a_hierarchical_board_can_be_contracted_by_leaf_name() -> None:
    """Before resolution this failed with 'there is no net called SENSE'."""
    spec = Spec(source="x", rules=[net("SENSE", ["R3.2"])])
    assert check_spec(spec, _hier()).verdict == "pass"


def test_the_full_path_works_too() -> None:
    spec = Spec(source="x", rules=[net("/Aux/SENSE", ["R3.2"])])
    assert check_spec(spec, _hier()).verdict == "pass"


def test_an_ambiguous_rule_fails_and_names_the_candidates() -> None:
    spec = Spec(source="x", rules=[net("OUT", ["R1.2"])])
    report = check_spec(spec, _hier())

    assert report.verdict == "fail"
    (result,) = report.failures
    assert "/Channel1/OUT" in result.detail and "/Channel2/OUT" in result.detail


def test_ambiguity_pre_empts_forbid_rather_than_reading_as_a_short() -> None:
    """A name matching two nets is a contract defect, not a finding about the board."""
    spec = Spec(source="x", rules=[forbid("OUT", "VIN")])
    report = check_spec(spec, _hier())

    assert report.verdict == "fail"
    assert "matches" in report.failures[0].detail
    assert "short" not in report.failures[0].detail.lower()


def test_a_net_that_is_genuinely_absent_is_still_reported_as_absent() -> None:
    """Absence and ambiguity are different answers and must not be conflated."""
    spec = Spec(source="x", rules=[net("NOPE", ["R3.2"])])
    report = check_spec(spec, _hier())

    assert report.verdict == "fail"
    assert "no net called" in report.failures[0].detail


def test_an_exact_name_beats_a_leaf_of_the_same_spelling() -> None:
    """A design with both `VIN` and `/Sheet/VIN` must resolve `VIN` to the root net."""
    nl = _hier()
    assert find_nets(nl, "VIN") == ("VIN",), "the exact match wins and does not go ambiguous"
