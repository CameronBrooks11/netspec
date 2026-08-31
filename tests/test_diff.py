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
    """
    before = _nl(_net("VIN", _n("C1", "1"), _n("R1", "1")))
    after = _nl(_net("VIN", _n("C1", "2"), _n("R1", "1")))
    result = diff_netlists(before, after)

    (swap,) = result.pin_swaps
    assert swap.ref == "C1"
    assert swap.was.pin == "1"
    assert swap.now.pin == "2"
    assert swap.polarity_risk, "a 1<->2 swap reverses a two-pin part"
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
