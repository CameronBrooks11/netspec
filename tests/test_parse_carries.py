"""Facts KiCad puts in the netlist that netspec used to throw away.

Both are inputs the contract vocabulary needs: a net's *classes* are what a power-domain
or differential-pair rule is written against, and a component's *sheet* is what lets a
rule tell two instances of a repeated block apart.

Neither is invented -- KiCad emits both -- but neither is as simple as it looks, and the
tests below exist mostly to pin down the ways they are not.
"""

from __future__ import annotations

from pathlib import Path

from kicad_netspec.parse import parse_kicadxml, parse_kicadxml_file

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str):
    return parse_kicadxml_file(FIXTURES / f"{name}.expected.xml")


# -- net classes: a list, not a name ----------------------------------------------------


def test_a_net_belongs_to_every_class_kicad_lists() -> None:
    """KiCad always appends Default, so a rule must test membership, never equality."""
    nl = _fixture("netclasses")

    assert nl.nets["GND"].netclasses == ("Power", "Default")
    assert "Power" in nl.nets["GND"].netclasses
    assert nl.nets["GND"].netclasses != ("Power",), "equality is the trap this shape prevents"


def test_a_net_in_no_declared_class_is_still_in_Default() -> None:
    nl = _fixture("netclasses")
    assert nl.nets["+3V3"].netclasses == ("Default",)


def test_a_class_name_containing_a_comma_cannot_be_recovered() -> None:
    """KiCad joins classes with ',' and does not escape them. This is lossy at the source.

    The fixture's project really does declare a class named ``Power, Fast``; KiCad writes
    ``class="Power, Fast,Default"``, which is indistinguishable from three classes. netspec
    reports the split as-is rather than stripping, so the stray leading space survives as
    the only clue that something was lost.
    """
    nl = _fixture("netclasses")
    assert nl.nets["VIN"].netclasses == ("Power", " Fast", "Default")


def test_a_netlist_with_no_classes_at_all_reports_none() -> None:
    doc = """<export version="E">
      <components><comp ref="R1"><value>1k</value></comp></components>
      <nets><net code="1" name="VIN"><node ref="R1" pin="1"/></net></nets>
    </export>"""
    nl = parse_kicadxml(doc)
    assert nl.nets["VIN"].netclasses == ()
    assert nl.components["R1"].sheet == "", "no <sheetpath> means no sheet, not a guess"


# -- sheet ------------------------------------------------------------------------------


def test_a_flat_design_puts_every_component_on_the_root_sheet() -> None:
    assert {c.sheet for c in _fixture("good_ldo").components.values()} == {"/"}


def test_a_hierarchical_design_separates_the_repeated_block() -> None:
    """The separation a symmetry rule needs: which instance a part belongs to."""
    nl = _fixture("hierarchy")

    by_sheet: dict[str, set[str]] = {}
    for c in nl.components.values():
        by_sheet.setdefault(c.sheet, set()).add(c.ref)

    assert by_sheet == {"/Channel1/": {"R1"}, "/Channel2/": {"R2"}, "/Aux/": {"R3"}}


# -- what is deliberately NOT carried ---------------------------------------------------
#
# Asserted against parsed *values*, not attribute names. An earlier version of these
# checked `hasattr(Net, "code")`, which passed happily while a scratch build carried the
# net code and the sheet UUID under different names -- a test that guarded a spelling.


def test_no_parsed_field_carries_a_uuid() -> None:
    """<sheetpath> also offers tstamps="/<uuid>/". D11 keeps identifiers like that out."""
    import re

    uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    nl = _fixture("hierarchy")

    seen = [
        str(v) for item in (*nl.components.values(), *nl.nets.values()) for v in vars(item).values()
    ]
    assert not [s for s in seen if uuid.search(s)], "a UUID reached the model"


def test_no_parsed_field_carries_kicads_net_number() -> None:
    """<net code> is renumbered by ordinary edits, so a contract must never see it."""
    nl = _fixture("hierarchy")
    codes = {"1", "2", "3", "4", "6"}  # the codes in that fixture

    for net in nl.nets.values():
        assert codes.isdisjoint({str(v) for v in vars(net).values()})


# -- neither belongs in a snapshot ------------------------------------------------------


def test_a_snapshot_holds_what_the_diff_compares_and_nothing_else() -> None:
    """Persisting a fact the diff ignores puts a committed snapshot at odds with netspec.

    A repo that commits snapshots would get a red ``git diff`` beside a green ``netspec
    diff``, with nothing naming the cause -- the "spurious diff" snapshot.py's own
    docstring promises cannot happen. Sheet is worse than merely uncompared: it is not
    stable (see Component.sheet).
    """
    import json

    from kicad_netspec.snapshot import dumps

    payload = json.loads(dumps(_fixture("hierarchy")))
    assert all("sheet" not in c for c in payload["components"])
    assert all("netclass" not in n and "netclasses" not in n for n in payload["nets"])


def test_the_snapshot_schema_did_not_have_to_change() -> None:
    from kicad_netspec.snapshot import SNAPSHOT_SCHEMA

    assert SNAPSHOT_SCHEMA == 1, "nothing was added to the on-disk shape, so nothing broke"


# -- the diff must not notice ------------------------------------------------------------


def test_neither_field_manufactures_a_connectivity_change() -> None:
    """Adding fields to a frozen dataclass changes __eq__. The diff must stay node-based."""
    from kicad_netspec.diff import diff_netlists
    from kicad_netspec.model import Component, Net, Node, build_netlist

    def board(sheet: str, classes: tuple[str, ...]):
        return build_netlist(
            [Net("VIN", frozenset({Node("R1", "1"), Node("R2", "1")}), netclasses=classes)],
            [Component("R1", "1k", sheet=sheet), Component("R2", "1k", sheet=sheet)],
        )

    result = diff_netlists(board("/A/", ("Default",)), board("/B/", ("Power", "Default")))
    assert result.empty, "reclassifying or re-sheeting is not a change in what connects"
