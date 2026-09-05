"""Facts KiCad puts in the netlist that netspec used to throw away.

Both are prerequisites for the contract vocabulary: a net's *class* is what a
power-domain or differential-pair rule is written against, and a component's *sheet* is
what lets a rule compare two instances of a repeated block.

Neither is invented -- KiCad emits both on every component and every net, and the parser
simply dropped them on the floor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kicad_netspec.parse import parse_kicadxml, parse_kicadxml_file
from kicad_netspec.snapshot import SNAPSHOT_SCHEMA, dumps, loads

FIXTURES = Path(__file__).parent / "fixtures"


def _hier():
    return parse_kicadxml_file(FIXTURES / "hierarchy.expected.xml")


# -- net class -------------------------------------------------------------------------


def test_a_nets_class_is_carried() -> None:
    """<net code="1" name="+3V3" class="Default"> -- the class was discarded."""
    nl = parse_kicadxml_file(FIXTURES / "good_ldo.expected.xml")
    assert nl.nets["VIN"].netclass == "Default"


def test_a_net_with_no_class_reports_an_empty_one() -> None:
    doc = """<export version="E">
      <components><comp ref="R1"><value>1k</value></comp></components>
      <nets><net code="1" name="VIN"><node ref="R1" pin="1"/></net></nets>
    </export>"""
    assert parse_kicadxml(doc).nets["VIN"].netclass == ""


# -- sheet path ------------------------------------------------------------------------


def test_a_flat_design_puts_every_component_on_the_root_sheet() -> None:
    nl = parse_kicadxml_file(FIXTURES / "good_ldo.expected.xml")
    assert {c.sheet for c in nl.components.values()} == {"/"}


def test_a_hierarchical_design_reports_which_sheet_each_part_is_on() -> None:
    """The input `mirrors()` needs: which instance of a repeated block a part belongs to."""
    nl = _hier()
    assert nl.components["R1"].sheet == "/Channel1/"
    assert nl.components["R2"].sheet == "/Channel2/"
    assert nl.components["R3"].sheet == "/Aux/"


def test_the_two_channels_are_separable_by_sheet() -> None:
    nl = _hier()
    by_sheet: dict[str, list[str]] = {}
    for c in nl.components.values():
        by_sheet.setdefault(c.sheet, []).append(c.ref)
    assert sorted(by_sheet) == ["/Aux/", "/Channel1/", "/Channel2/"]


# -- what is deliberately NOT carried ---------------------------------------------------


def test_the_net_code_is_not_carried() -> None:
    """KiCad's net *number* is renumbered by ordinary edits, so a contract must not see it.

    netspec is coordinate-free and UUID-free for the same reason (D11): an identifier
    that moves on its own turns a stable assertion into a flaky one.
    """
    from kicad_netspec.model import Net

    assert not hasattr(Net("N", frozenset()), "code")


def test_the_sheet_timestamp_path_is_not_carried() -> None:
    """<sheetpath> also carries tstamps="/<uuid>/". UUIDs are the thing D11 rejects."""
    from kicad_netspec.model import Component

    assert not hasattr(Component("R1"), "tstamps")


# -- snapshots --------------------------------------------------------------------------


def test_a_snapshot_round_trips_the_new_fields() -> None:
    original = _hier()
    restored = loads(dumps(original))
    assert restored.components["R1"].sheet == "/Channel1/"
    assert restored.nets["/Channel1/OUT"].netclass == "Default"
    assert restored.nets == original.nets
    assert restored.components == original.components


def test_the_schema_version_was_raised() -> None:
    assert SNAPSHOT_SCHEMA >= 2, "adding persisted fields is a schema change"


def test_an_older_snapshot_still_loads() -> None:
    """Schema 1 knew neither field. It must still read, with them simply absent."""
    old = json.dumps(
        {
            "schema": 1,
            "source": "board.kicad_sch",
            "kicad_version": "9.0",
            "components": [{"ref": "R1", "value": "1k", "footprint": None, "lib_id": "Device:R"}],
            "nets": [
                {
                    "name": "VIN",
                    "nodes": [{"ref": "R1", "pin": "1", "function": None, "type": None}],
                }
            ],
        }
    )
    nl = loads(old)
    assert nl.components["R1"].sheet == ""
    assert nl.nets["VIN"].netclass == ""


def test_a_newer_snapshot_is_still_refused() -> None:
    from kicad_netspec.snapshot import SnapshotError

    with pytest.raises(SnapshotError):
        loads(json.dumps({"schema": SNAPSHOT_SCHEMA + 1, "nets": [], "components": []}))


# -- the diff must not notice ------------------------------------------------------------


def test_a_class_change_alone_is_not_a_connectivity_change() -> None:
    """Adding fields to a frozen dataclass changes __eq__. The diff must stay node-based."""
    from kicad_netspec.diff import diff_netlists
    from kicad_netspec.model import Component, Net, Node, build_netlist

    def board(netclass: str):
        return build_netlist(
            [Net("VIN", frozenset({Node("R1", "1"), Node("R2", "1")}), netclass=netclass)],
            [Component("R1", "1k"), Component("R2", "1k")],
        )

    result = diff_netlists(board("Default"), board("Power"))
    assert result.empty, "a netclass rename is not a change in what is connected to what"
