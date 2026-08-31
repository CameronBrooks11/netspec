"""Diff engine tests. No KiCad needed -- these build netlists directly."""

from __future__ import annotations

from kicad_netspec.diff import diff_netlists
from kicad_netspec.model import Component, Net, Netlist, Node, build_netlist


def _n(ref: str, pin: str, function: str | None = None) -> Node:
    return Node(ref=ref, pin=pin, function=function)


def _net(name: str, *nodes: Node) -> Net:
    return Net(name=name, nodes=frozenset(nodes))


def _nl(*nets: Net, components: tuple[Component, ...] = ()) -> Netlist:
    refs = {n.ref for net in nets for n in net.nodes}
    return build_netlist(nets, components or tuple(Component(ref=r) for r in sorted(refs)))


# -- the null case ---------------------------------------------------------------------


def test_identical_netlists_diff_to_nothing() -> None:
    a = _nl(_net("VIN", _n("C1", "1"), _n("U1", "3")))
    assert diff_netlists(a, a).empty


def test_node_order_is_not_a_change() -> None:
    a = _nl(_net("VIN", _n("C1", "1"), _n("U1", "3")))
    b = _nl(_net("VIN", _n("U1", "3"), _n("C1", "1")))
    assert diff_netlists(a, b).empty


# -- ordinary edits --------------------------------------------------------------------


def test_a_pin_joining_a_named_net_is_reported() -> None:
    before = _nl(_net("VIN", _n("C1", "1"), _n("U1", "3")))
    after = _nl(_net("VIN", _n("C1", "1"), _n("U1", "3"), _n("C7", "1")))
    result = diff_netlists(before, after)

    (change,) = result.structural
    assert change.kind == "modified"
    assert change.name == "VIN"
    assert change.nodes_added == frozenset({_n("C7", "1")})
    assert not change.nodes_removed
    assert not result.pin_swaps


def test_added_and_removed_nets() -> None:
    before = _nl(_net("VIN", _n("C1", "1"), _n("U1", "3")))
    after = _nl(_net("+3V3", _n("C2", "1"), _n("U1", "2")))
    kinds = {c.name: c.kind for c in diff_netlists(before, after).structural}
    assert kinds == {"VIN": "removed", "+3V3": "added"}


def test_component_value_and_footprint_changes() -> None:
    before = _nl(
        _net("VIN", _n("C1", "1"), _n("U1", "3")),
        components=(Component("C1", "10uF", "C_0805"), Component("U1", "AMS1117")),
    )
    after = _nl(
        _net("VIN", _n("C1", "1"), _n("U1", "3")),
        components=(Component("C1", "22uF", "C_0603"), Component("U1", "AMS1117")),
    )
    changes = {
        (c.ref, c.kind): (c.before, c.after) for c in diff_netlists(before, after).component_changes
    }
    assert changes[("C1", "value")] == ("10uF", "22uF")
    assert changes[("C1", "footprint")] == ("C_0805", "C_0603")


# -- the noise problem D11 exists to solve ---------------------------------------------


def test_an_anonymous_rename_with_no_membership_change_is_benign() -> None:
    """KiCad renames unlabelled nets after their contents. That alone is not an edit."""
    before = _nl(_net("Net-(U2-Pad3)", _n("U2", "3"), _n("R9", "1")))
    after = _nl(_net("Net-(U2-Pad1)", _n("U2", "3"), _n("R9", "1")))
    result = diff_netlists(before, after)

    assert result.empty, "a pure rename carries no electrical meaning"
    (rename,) = result.benign
    assert rename.kind == "renamed"
    assert rename.previous_name == "Net-(U2-Pad3)"


def test_an_anonymous_net_that_really_changed_is_not_benign() -> None:
    before = _nl(_net("Net-(U2-Pad3)", _n("U2", "3"), _n("R9", "1")))
    after = _nl(_net("Net-(U2-Pad3)", _n("U2", "3"), _n("R9", "1"), _n("C4", "2")))
    result = diff_netlists(before, after)

    assert not result.empty
    (change,) = result.structural
    assert change.nodes_added == frozenset({_n("C4", "2")})


def test_matching_by_membership_does_not_double_report() -> None:
    """The failure a name-keyed diff produces: one edit shown as a removal and an add."""
    before = _nl(_net("Net-(C1-Pad1)", _n("C1", "1"), _n("R1", "1")))
    after = _nl(_net("Net-(C1-Pad1)", _n("C1", "1"), _n("R1", "1"), _n("R2", "1")))
    result = diff_netlists(before, after)
    assert len(result.structural) == 1, "one edit must read as one change"


# -- the derived signal ----------------------------------------------------------------


def test_pin_swap_is_detected_and_flagged() -> None:
    """The polarised-capacitor bug, in miniature.

    The net keeps its name and its size; only *which pin of C1* is on it changed. Every
    coarser check passes, which is exactly why this needs its own signal.

    The part must be identified for the swap to count as a defect: an identical topology
    on a connector is a deliberate re-pin, not a reversal (D15).
    """
    parts = [Component("C1", lib_id="Device:C_Polarized"), Component("R1", lib_id="Device:R")]
    before = build_netlist([_net("VIN", _n("C1", "1"), _n("R1", "1"))], parts)
    after = build_netlist([_net("VIN", _n("C1", "2"), _n("R1", "1"))], parts)
    result = diff_netlists(before, after)

    (swap,) = result.pin_swaps
    assert swap.ref == "C1"
    assert swap.was.pin == "1"
    assert swap.now.pin == "2"
    assert swap.polarity_risk, "reversing a polarised capacitor is a defect"
    assert result.suspicious == result.pin_swaps


def test_a_swap_on_a_multi_pin_part_is_reported_without_the_polarity_warning() -> None:
    before = _nl(_net("VIN", _n("U1", "3"), _n("R1", "1")))
    after = _nl(_net("VIN", _n("U1", "7"), _n("R1", "1")))
    (swap,) = diff_netlists(before, after).pin_swaps
    assert swap.ref == "U1"
    assert not swap.polarity_risk


def test_moving_a_pin_between_different_parts_is_not_a_swap() -> None:
    """Two different components changing is an ordinary edit, not the sign-error shape."""
    before = _nl(_net("VIN", _n("C1", "1"), _n("R1", "1")))
    after = _nl(_net("VIN", _n("C2", "1"), _n("R1", "1")))
    result = diff_netlists(before, after)
    assert not result.pin_swaps
    assert result.structural


# -- what a pin swap actually means (D15) ----------------------------------------------


def _swap(lib_id: str, was_fn: str | None = None, now_fn: str | None = None):
    before = _nl(_net("A", Node("X", "1", was_fn), _n("R9", "1")))
    after = _nl(_net("A", Node("X", "2", now_fn), _n("R9", "1")))
    before = build_netlist(before.nets.values(), [Component("X", lib_id=lib_id), Component("R9")])
    after = build_netlist(after.nets.values(), [Component("X", lib_id=lib_id), Component("R9")])
    (swap,) = diff_netlists(before, after).pin_swaps
    return swap


def test_a_swap_on_a_polarised_part_is_a_defect() -> None:
    for lib in ("Device:C_Polarized", "Device:CP", "Device:LED", "Device:D_Schottky"):
        assert _swap(lib).significance == "polarity", lib


def test_pin_names_declare_polarity_whatever_the_symbol_is_called() -> None:
    """Device:D names its pins K and A; that beats any name heuristic."""
    assert _swap("SomeVendor:CustomDiode", "K_1", "A_2").significance == "polarity"


def test_a_swap_on_a_symmetric_passive_means_nothing() -> None:
    for lib in ("Device:R", "Device:C", "Device:L", "Device:R_Small"):
        assert _swap(lib).significance == "none", lib


def test_a_swap_on_a_connector_or_ic_is_a_repin_not_a_defect() -> None:
    for lib in ("Connector_Generic:Conn_01x02", "Driver_Motor:LMD18200", "Device:Device_Thing"):
        assert _swap(lib).significance == "repin", lib


def test_an_analog_pin_is_not_an_anode() -> None:
    """Regression: rstrip with a character set reduced 'a6_25' to 'a' and matched anode.

    Found on a real board, where an Arduino Nano's A6/A7 re-pin was reported as a
    reversed part.
    """
    swap = _swap("MCU_Module:Arduino_Nano_v3.x", "A6_25", "A7_26")
    assert swap.significance == "repin"


def test_device_prefixed_symbols_are_not_all_diodes() -> None:
    """Regression: a bare 'd' prefix matched Device, Driver, DIP and everything else."""
    for lib in ("Device:Device_Thing", "Driver_Motor:DRV8871", "Package_DIP:DIP-8"):
        assert _swap(lib).significance != "polarity", lib


def test_only_polarity_swaps_are_suspicious() -> None:
    """Measured: a real re-pinning commit produced 14 swaps and no defects.

    Warning on all of them would train a reader to ignore the warning.
    """
    before = _nl(_net("A", _n("J2", "1"), _n("R9", "1")))
    after = _nl(_net("A", _n("J2", "2"), _n("R9", "1")))
    before = build_netlist(before.nets.values(), [Component("J2", lib_id="Connector:Conn_01x02")])
    after = build_netlist(after.nets.values(), [Component("J2", lib_id="Connector:Conn_01x02")])
    result = diff_netlists(before, after)
    assert result.pin_swaps, "the swap is still reported"
    assert not result.suspicious, "but it is not flagged as a defect"
