"""Snapshots must round-trip exactly and be byte-stable."""

from __future__ import annotations

import pytest

from kicad_netspec import snapshot
from kicad_netspec.diff import diff_netlists
from kicad_netspec.model import Component, Net, Node, build_netlist


def _sample():
    return build_netlist(
        [
            Net("VIN", frozenset({Node("C1", "1"), Node("U1", "3", "VI_3", "power_in")})),
            Net("GND", frozenset({Node("C1", "2"), Node("U1", "1", "GND_1", "power_in")})),
        ],
        [Component("C1", "10uF", "C_0805"), Component("U1", "AMS1117-3.3")],
        source="board.kicad_sch",
        kicad_version="Eeschema 10.0.5",
    )


def test_round_trip_preserves_everything_that_matters() -> None:
    original = _sample()
    restored = snapshot.loads(snapshot.dumps(original))

    assert restored.nets == original.nets
    assert restored.components == original.components
    assert restored.source == original.source
    assert diff_netlists(original, restored).empty


def test_round_trip_preserves_pin_functions() -> None:
    restored = snapshot.loads(snapshot.dumps(_sample()))
    assert str(restored.resolve("U1.VI")) == "U1.3"


def test_output_is_byte_stable() -> None:
    """A snapshot is meant to live in git; an unchanged design must not churn."""
    assert snapshot.dumps(_sample()) == snapshot.dumps(_sample())


def test_node_order_does_not_affect_bytes() -> None:
    a = build_netlist([Net("N", frozenset({Node("A", "1"), Node("B", "2")}))], [])
    b = build_netlist([Net("N", frozenset({Node("B", "2"), Node("A", "1")}))], [])
    assert snapshot.dumps(a) == snapshot.dumps(b)


def test_refuses_a_newer_schema() -> None:
    payload = '{"schema": 9999, "nets": [], "components": []}'
    with pytest.raises(snapshot.SnapshotError, match="newer"):
        snapshot.loads(payload)


def test_refuses_something_that_is_not_a_snapshot() -> None:
    with pytest.raises(snapshot.SnapshotError, match="no schema"):
        snapshot.loads('{"nets": []}')


def test_write_and_read(tmp_path) -> None:
    path = snapshot.write(_sample(), tmp_path / "nested" / "snap.json")
    assert path.exists()
    assert diff_netlists(snapshot.read(path), _sample()).empty


def test_a_check_report_is_not_mistaken_for_a_snapshot() -> None:
    """Both are JSON with a `schema`. Loading a report as a snapshot found no nets, so
    `netspec diff` on two reports answered "no change in connectivity" and exited 0 --
    green, confident, and about something other than what was asked."""
    import json as _json

    from kicad_netspec.check import check_spec
    from kicad_netspec.contract import Spec, net
    from kicad_netspec.model import Component, Net, Node, build_netlist
    from kicad_netspec.report import check_report

    nl = build_netlist([Net("VIN", frozenset({Node("R1", "1")}))], [Component("R1", "1k")])
    document = check_report(check_spec(Spec(source="b", rules=[net("VIN", ["R1.1"])]), nl))

    with pytest.raises(snapshot.SnapshotError, match="not a snapshot"):
        snapshot.loads(_json.dumps(document))
