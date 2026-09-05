"""Tests that need a real KiCad. Skipped cleanly when there is not one.

These re-derive from a live engine the same facts ``test_fixtures.py`` asserts against
recorded ground truth, so a KiCad upgrade that changes an answer is caught.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_netspec.oracle import Cli10Backend, EnvironmentError_, KiCadNotFound, find_kicad_cli

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def oracle() -> Cli10Backend:
    try:
        return Cli10Backend(find_kicad_cli())
    except (KiCadNotFound, EnvironmentError_) as exc:
        pytest.skip(f"no KiCad available: {exc}")


def test_discovery_reports_a_usable_engine(oracle: Cli10Backend) -> None:
    caps = oracle.capabilities()
    assert caps.netlist and caps.erc and caps.drc and caps.schematic_parity
    assert caps.kicad_version, "the engine must report a version"


def test_live_netlist_matches_recorded_ground_truth(oracle: Cli10Backend) -> None:
    """The whole project rests on this: our reading equals KiCad's."""
    from kicad_netspec.parse import parse_kicadxml_file

    for name, connected in (
        ("good_ldo", 3),
        ("dangling_wires", 0),
        ("swapped_pins", 1),
        ("reversed_polarized_cap", 1),
        ("hierarchy", 1),
        ("netclasses", 3),
    ):
        live = oracle.netlist(FIXTURES / f"{name}.kicad_sch")
        recorded = parse_kicadxml_file(FIXTURES / f"{name}.expected.xml")

        # Split, because `live.nets == recorded.nets` compares whole frozen Net objects
        # -- including netclasses. A class-only mismatch failing under a message about
        # connectivity sends the reader to the wrong place.
        assert {n.name: n.nodes for n in live.nets.values()} == {
            n.name: n.nodes for n in recorded.nets.values()
        }, f"{name}: live KiCad disagrees with the fixture about what is connected"
        assert {n.name: n.netclasses for n in live.nets.values()} == {
            n.name: n.netclasses for n in recorded.nets.values()
        }, f"{name}: live KiCad disagrees with the fixture about net classes"
        assert live.components == recorded.components, (
            f"{name}: live KiCad disagrees with the fixture about components or sheets"
        )
        assert len(live.connected_nets) == connected


def test_erc_runs_at_every_severity(oracle: Cli10Backend) -> None:
    """DECISIONS D13: never let KiCad's default severities decide silently."""
    report = oracle.erc(FIXTURES / "dangling_wires.kicad_sch")
    assert report.severities_requested == ("error", "warning", "exclusion")
    assert report.findings, "a schematic with seven floating pins must report something"


def test_a_missing_file_is_an_environment_fault_not_a_finding(oracle: Cli10Backend) -> None:
    """DECISIONS D10. A file that is not there says nothing about any design."""
    with pytest.raises(EnvironmentError_):
        oracle.netlist(FIXTURES / "does-not-exist.kicad_sch")


def test_netlist_reports_the_design_as_its_source(oracle: Cli10Backend) -> None:
    """Not the throwaway file KiCad wrote on the way -- consumers key on this."""
    sch = FIXTURES / "good_ldo.kicad_sch"
    assert oracle.netlist(sch).source == str(sch.resolve())
