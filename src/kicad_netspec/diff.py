"""Compare two netlists and say what actually changed.

The hard part is not computing a delta, it is producing one a human will read. KiCad
names an unlabelled net after its own contents, so changing connectivity also changes the
name; a naive name-keyed diff reports every such net twice -- once as a removal and once
as an addition -- and buries the one edit that mattered.

So nets are matched in two passes (DECISIONS D11):

* **Named** nets -- a label a person wrote -- are matched by name. The name is the
  identity; a change in membership under a stable name is the signal.
* **Anonymous** nets are matched by how much membership they share, because their names
  are descriptions rather than identities. A name that changed while membership did not
  is reported as benign.

On top of the delta sits one derived signal, :class:`PinSwap`, which exists because of a
specific bug that ships: a schematic writer with a sign error moves a connection from one
pin of a part to the other. Connectivity still exists, the net keeps its name, and the
board is wrong. On a polarised part that is a reversed capacitor, and ERC has no rule
against it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from kicad_netspec.model import Component, Net, Netlist, Node

__all__ = [
    "ComponentChange",
    "NetChange",
    "NetlistDiff",
    "PinSwap",
    "diff_netlists",
]

NetChangeKind = Literal["added", "removed", "modified", "renamed"]
ComponentChangeKind = Literal["added", "removed", "value", "footprint"]


@dataclass(frozen=True)
class NetChange:
    """One net that is not the same in both netlists."""

    name: str
    kind: NetChangeKind
    nodes_added: frozenset[Node] = frozenset()
    nodes_removed: frozenset[Node] = frozenset()
    previous_name: str | None = None
    """Set when an anonymous net kept its membership but changed name."""

    @property
    def benign(self) -> bool:
        """A rename with no change in membership carries no electrical meaning."""
        return self.kind == "renamed" and not self.nodes_added and not self.nodes_removed

    def __str__(self) -> str:
        if self.kind == "renamed":
            return f"~ {self.previous_name} -> {self.name} (rename only)"
        marks = [f"+{n}" for n in sorted(self.nodes_added)]
        marks += [f"-{n}" for n in sorted(self.nodes_removed)]
        sigil = {"added": "+", "removed": "-", "modified": "~"}[self.kind]
        return f"{sigil} {self.name}" + (f"  {' '.join(marks)}" if marks else "")


@dataclass(frozen=True)
class ComponentChange:
    ref: str
    kind: ComponentChangeKind
    before: str | None = None
    after: str | None = None

    def __str__(self) -> str:
        if self.kind in ("added", "removed"):
            sigil = "+" if self.kind == "added" else "-"
            return f"{sigil} {self.ref}" + (
                f"  {self.after or self.before}" if (self.after or self.before) else ""
            )
        return f"~ {self.ref}  {self.kind}: {self.before!r} -> {self.after!r}"


# Two-terminal parts whose terminals are interchangeable. Swapping their pins changes
# nothing electrically, so reporting it is pure noise.
_SYMMETRIC = frozenset({"r", "r_small", "c", "c_small", "l", "l_small", "r_us", "c_us"})

# Parts whose two terminals are *not* interchangeable, so a swap reverses the part.
# Anchored deliberately: a bare "d" prefix would also match Device, Driver, DIP and
# every other symbol that happens to start with the letter.
_POLARISED_SYMBOL = re.compile(
    r"^(c_polarized|cp|d|led|battery|d_schottky|d_zener|d_tvs|d_photo)(_.*)?$"
)

# Pin names that declare polarity outright, whatever the symbol is called -- more
# reliable than a name match where the library provides them, since Device:D names its
# pins K and A.
_POLARISED_PIN_NAMES = frozenset({"a", "k", "+", "-", "anode", "cathode"})

# KiCad suffixes a pin function with its pin number: "K_1", "A_2". Strip exactly one
# such suffix. (str.rstrip with a character set would take "a6_25" all the way down to
# "a" and call an Arduino's analog pin an anode.)
_PIN_SUFFIX = re.compile(r"^(.*?)_\d+$")

Significance = Literal["polarity", "repin", "none"]


def _symbol_name(lib_id: str | None) -> str:
    return (lib_id or "").rpartition(":")[2].casefold()


def _pin_name(function: str | None) -> str:
    name = (function or "").casefold()
    match = _PIN_SUFFIX.match(name)
    return match.group(1) if match else name


@dataclass(frozen=True)
class PinSwap:
    """A connection moved from one pin of a part to another, on the same net.

    Whether that matters depends entirely on **the part**, not on the topology. A
    reversed electrolytic capacitor and a deliberately re-pinned connector produce an
    identical diff: two pins trading nets. Every swap in a genuine re-pinning commit
    paired up exactly the way a reversed capacitor does, so pairing cannot separate them
    -- see ``significance``.

    That observation comes from running this diff across the history of one real
    dual-channel board, which is not public and so is not reproducible from this repo.
    Recorded as the origin of the rule rather than as a figure anyone can check;
    ``tests/test_diff.py`` pins the cases it produced.
    """

    ref: str
    net: str
    was: Node
    now: Node
    lib_id: str | None = None

    @property
    def significance(self) -> Significance:
        """How much this swap matters, judged from the component.

        ``polarity``
            The part's terminals are not interchangeable -- a polarised capacitor, a
            diode, an LED. The swap reverses it. ERC will not report this, because
            reversing a polarised part is electrically legal wiring.
        ``none``
            A symmetric two-terminal passive. Swapping a resistor's pins changes
            nothing, so this is not worth a reader's attention.
        ``repin``
            Anything else -- a connector, an IC. A real change, and usually a
            deliberate one, so it is reported without a warning.
        """
        if {_pin_name(n.function) for n in (self.was, self.now)} & _POLARISED_PIN_NAMES:
            return "polarity"
        name = _symbol_name(self.lib_id)
        if name in _SYMMETRIC:
            return "none"
        if _POLARISED_SYMBOL.match(name):
            return "polarity"
        return "repin"

    @property
    def polarity_risk(self) -> bool:
        """True when this swap reverses a part whose terminals are not interchangeable."""
        return self.significance == "polarity"

    def __str__(self) -> str:
        note = {
            "polarity": "  <- REVERSES a polarised part",
            "none": "  (symmetric part; no electrical effect)",
            "repin": "",
        }[self.significance]
        return f"{self.ref} on {self.net}: pin {self.was.pin} -> pin {self.now.pin}{note}"


@dataclass(frozen=True)
class NetlistDiff:
    """Everything that changed between two readings of a design."""

    net_changes: tuple[NetChange, ...] = ()
    component_changes: tuple[ComponentChange, ...] = ()
    pin_swaps: tuple[PinSwap, ...] = ()
    now_floating: tuple[Node, ...] = ()
    """Pins that were connected and no longer are. Usually a regression."""

    no_longer_floating: tuple[Node, ...] = ()
    """Pins that were floating and are now connected."""

    before_source: str = ""
    after_source: str = ""

    @property
    def structural(self) -> tuple[NetChange, ...]:
        """Net changes that carry electrical meaning, i.e. excluding pure renames."""
        return tuple(c for c in self.net_changes if not c.benign)

    @property
    def benign(self) -> tuple[NetChange, ...]:
        return tuple(c for c in self.net_changes if c.benign)

    @property
    def empty(self) -> bool:
        """True when nothing electrically meaningful changed."""
        return not (
            self.structural
            or self.component_changes
            or self.pin_swaps
            or self.now_floating
            or self.no_longer_floating
        )

    @property
    def suspicious(self) -> tuple[PinSwap, ...]:
        """Swaps that reverse a part whose terminals are not interchangeable.

        Deliberately narrower than ``pin_swaps``. A single re-pinning commit on the
        board described in :class:`PinSwap` moved fourteen connections at once and not
        one of them was a defect -- a measurement on a design that is not public, so take
        the number as the reason for the rule rather than as something to verify here.
        Warning on all of them would train a reader to ignore the warning.
        """
        return tuple(s for s in self.pin_swaps if s.significance == "polarity")

    @property
    def meaningful_swaps(self) -> tuple[PinSwap, ...]:
        """Swaps worth showing at all -- everything but symmetric two-terminal passives."""
        return tuple(s for s in self.pin_swaps if s.significance != "none")

    def __str__(self) -> str:
        return (
            f"NetlistDiff({len(self.structural)} net changes, "
            f"{len(self.component_changes)} component changes, "
            f"{len(self.pin_swaps)} pin swaps)"
        )


def diff_netlists(before: Netlist, after: Netlist) -> NetlistDiff:
    """Compare two netlists. Order matters: ``before`` then ``after``."""
    net_changes, matched = _diff_nets(before, after)
    was_floating = set(before.isolated_nodes)
    is_floating = set(after.isolated_nodes)
    return NetlistDiff(
        net_changes=tuple(net_changes),
        component_changes=tuple(_diff_components(before.components, after.components)),
        pin_swaps=tuple(_pin_swaps(matched, after.components)),
        now_floating=tuple(sorted(is_floating - was_floating)),
        no_longer_floating=tuple(sorted(was_floating - is_floating)),
        before_source=before.source,
        after_source=after.source,
    )


# -- nets ------------------------------------------------------------------------------


def _diff_nets(before: Netlist, after: Netlist) -> tuple[list[NetChange], list[tuple[Net, Net]]]:
    changes: list[NetChange] = []
    matched: list[tuple[Net, Net]] = []

    # A one-node "net" is KiCad's way of listing a floating pin, not a net anyone
    # drew. Diffing them as nets turns a single edit into an add plus a removal, which
    # is the noise D11 exists to avoid; they are reported as floating-pin deltas instead.
    old_named = {n.name: n for n in before.named_nets if n.connected}
    new_named = {n.name: n for n in after.named_nets if n.connected}

    for name in sorted(old_named.keys() | new_named.keys()):
        old, new = old_named.get(name), new_named.get(name)
        if old is None and new is not None:
            changes.append(NetChange(name=name, kind="added", nodes_added=new.nodes))
        elif new is None and old is not None:
            changes.append(NetChange(name=name, kind="removed", nodes_removed=old.nodes))
        elif old is not None and new is not None:
            matched.append((old, new))
            if old.nodes != new.nodes:
                changes.append(
                    NetChange(
                        name=name,
                        kind="modified",
                        nodes_added=new.nodes - old.nodes,
                        nodes_removed=old.nodes - new.nodes,
                    )
                )

    changes.extend(
        _diff_anonymous(
            [n for n in before.anonymous_nets if n.connected],
            [n for n in after.anonymous_nets if n.connected],
            matched,
        )
    )
    return changes, matched


def _diff_anonymous(
    old_nets: Iterable[Net], new_nets: Iterable[Net], matched: list[tuple[Net, Net]]
) -> list[NetChange]:
    """Pair anonymous nets by shared membership, since their names are descriptions."""
    remaining = list(new_nets)
    changes: list[NetChange] = []

    for old in sorted(old_nets, key=lambda n: n.name):
        best, score = None, 0
        for candidate in remaining:
            overlap = len(old.nodes & candidate.nodes)
            if overlap > score:
                best, score = candidate, overlap

        if best is None:
            changes.append(NetChange(name=old.name, kind="removed", nodes_removed=old.nodes))
            continue

        remaining.remove(best)
        matched.append((old, best))
        if old.nodes == best.nodes:
            if old.name != best.name:
                changes.append(NetChange(name=best.name, kind="renamed", previous_name=old.name))
        else:
            changes.append(
                NetChange(
                    name=best.name,
                    kind="modified",
                    nodes_added=best.nodes - old.nodes,
                    nodes_removed=old.nodes - best.nodes,
                    previous_name=old.name if old.name != best.name else None,
                )
            )

    changes.extend(
        NetChange(name=new.name, kind="added", nodes_added=new.nodes)
        for new in sorted(remaining, key=lambda n: n.name)
    )
    return changes


# -- the derived signal ----------------------------------------------------------------


def _pin_swaps(
    matched: Iterable[tuple[Net, Net]], components: Mapping[str, Component]
) -> list[PinSwap]:
    """Find connections that moved between pins of the same part on the same net."""
    swaps: list[PinSwap] = []
    for old, new in matched:
        gone = old.nodes - new.nodes
        came = new.nodes - old.nodes
        for was in sorted(gone):
            for now in sorted(came):
                if was.ref == now.ref and was.pin != now.pin:
                    part = components.get(was.ref)
                    swaps.append(
                        PinSwap(
                            ref=was.ref,
                            net=new.name,
                            was=was,
                            now=now,
                            lib_id=part.lib_id if part else None,
                        )
                    )
                    break
    return swaps


# -- components ------------------------------------------------------------------------


def _diff_components(
    before: Mapping[str, Component], after: Mapping[str, Component]
) -> list[ComponentChange]:
    changes: list[ComponentChange] = []
    for ref in sorted(before.keys() | after.keys()):
        old, new = before.get(ref), after.get(ref)
        if old is None and new is not None:
            changes.append(ComponentChange(ref=ref, kind="added", after=new.value))
        elif new is None and old is not None:
            changes.append(ComponentChange(ref=ref, kind="removed", before=old.value))
        elif old is not None and new is not None:
            if old.value != new.value:
                changes.append(
                    ComponentChange(ref=ref, kind="value", before=old.value, after=new.value)
                )
            if old.footprint != new.footprint:
                changes.append(
                    ComponentChange(
                        ref=ref, kind="footprint", before=old.footprint, after=new.footprint
                    )
                )
    return changes
