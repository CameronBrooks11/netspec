"""The canonical model: what a design *is*, as KiCad reports it.

Deliberately coordinate-free and UUID-free. Two schematics that place the same parts at
different positions produce equal models, because position carries no electrical meaning.
Everything here is frozen and hashable, so comparison is set arithmetic.

Connectivity is exact (DECISIONS D9): a pin is on a net or it is not. There is no
threshold anywhere in this module, and there must never be one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "Component",
    "Net",
    "Netlist",
    "Node",
    "PinRef",
    "parse_pin_ref",
]

# KiCad names an unlabelled net after its own contents, e.g. "Net-(C1-Pad1)" or
# "unconnected-(U1-VI-Pad3)". Such a name is a description, not an identity: change the
# connectivity and the name changes with it. See DECISIONS D11.
_ANONYMOUS = re.compile(r"^(?:Net-|unconnected-)\(.*\)$")


@dataclass(frozen=True, order=True)
class Node:
    """One pin of one component, sitting on a net."""

    ref: str
    """Component reference designator, e.g. ``C1``."""

    pin: str
    """Pin number as KiCad reports it, e.g. ``1``. Not necessarily numeric."""

    function: str | None = None
    """Pin name from the symbol, e.g. ``VI_3``. KiCad's ``pinfunction``.

    Preferred over ``pin`` when writing a contract (DECISIONS D12): pin *numbers* are
    what the known editing bugs corrupt, while a function name survives both a
    sign-flipped coordinate helper and a library renumbering.
    """

    type: str | None = None
    """Electrical type, e.g. ``power_in``, ``passive``. KiCad's ``pintype``."""

    def __str__(self) -> str:
        return f"{self.ref}.{self.pin}"


@dataclass(frozen=True)
class Net:
    """A set of pins KiCad considers electrically joined."""

    name: str
    nodes: frozenset[Node]

    netclasses: tuple[str, ...] = ()
    """Every class this net belongs to, in the order KiCad lists them.

    A net can be in several. KiCad writes them joined by commas and always appends
    ``Default``, so a "Power" net reads ``class="Power,Default"`` -- meaning a rule must
    test *membership*, never equality. Storing this as a single string invited exactly
    that mistake.

    **The joining is not escaped**, and KiCad permits a comma inside a class name, so
    ``class="Power, Fast,Default"`` is ambiguous between one class named ``Power, Fast``
    and two named ``Power`` and `` Fast``. That information is destroyed by KiCad before
    netspec sees it and cannot be recovered here; the split is reported as-is, without
    stripping, so the stray leading space at least survives as a clue.

    Not part of what makes a net *the same net*: the diff compares membership, so a
    reclassification is not a connectivity change.
    """

    @property
    def anonymous(self) -> bool:
        """True when KiCad generated this name from the net's own contents (D11)."""
        return bool(_ANONYMOUS.match(self.name))

    @property
    def connected(self) -> bool:
        """True when this net actually joins two or more pins.

        A one-node net is KiCad's way of reporting an isolated pin. A schematic full of
        them looks fine and is wired to nothing -- the failure mode in
        ``tests/fixtures/dangling_wires.kicad_sch``.
        """
        return len(self.nodes) >= 2

    @property
    def identity(self) -> frozenset[Node]:
        """What this net *is*, independent of what it is called.

        Anonymous nets are compared by this rather than by name, so that a rename which
        follows a connectivity change is not mistaken for a second, separate change.
        """
        return self.nodes

    def __str__(self) -> str:
        return f"{self.name} [{', '.join(sorted(str(n) for n in self.nodes))}]"


@dataclass(frozen=True)
class Component:
    """A placed part, as it appears in the netlist."""

    ref: str
    value: str = ""
    footprint: str | None = None
    lib_id: str | None = None

    attributed_sheet: str = ""
    """The sheet KiCad attributes this part to -- ``/`` when flat, ``/Channel1/`` when
    not. Empty if the netlist omitted it.

    **One value, even for a part whose units sit on different sheets.** KiCad writes a
    single ``<sheetpath>`` per component and picks it by sheet traversal order, so a dual
    op-amp straddling two channels is attributed wholly to one of them, and reordering
    the pages -- an edit with no electrical meaning -- can change which. Read it as "a
    sheet this part appears on", never as "the sheet this part is on", and do not persist
    it: it is not stable enough to commit, which is why the snapshot omits it.

    Named ``attributed_sheet`` rather than ``sheet`` for that reason. A field called
    ``sheet`` reads as authoritative at every call site, and a docstring cannot un-say a
    name -- the caller has to be reminded, where they use it, that KiCad chose this.

    KiCad offers the same path in UUIDs (``tstamps``); that form is not carried at all,
    for the reason D11 gives for avoiding unstable identifiers.
    """

    def __str__(self) -> str:
        return f"{self.ref} ({self.value})" if self.value else self.ref


@dataclass(frozen=True)
class Netlist:
    """KiCad's opinion of one design, canonicalised.

    This is the only representation of "what the design is" that netspec compares
    against intent. It comes from an :class:`~kicad_netspec.oracle.base.Oracle`; nothing
    else in the package is permitted to construct one from a design file.
    """

    nets: Mapping[str, Net]
    components: Mapping[str, Component]
    source: str = ""
    kicad_version: str = ""
    _by_pin: Mapping[tuple[str, str], str] = field(default_factory=dict, repr=False)
    _by_ref: Mapping[str, tuple[Node, ...]] = field(default_factory=dict, repr=False)

    # -- lookups ---------------------------------------------------------------

    def net_of(self, ref: str, pin: str) -> str | None:
        """Name of the net a pin sits on, or None if there is no such pin.

        ``pin`` may be a number or the pin's *function* name, on the same terms as
        :meth:`resolve` -- because a contract that names pins the way D12 recommends
        should not have to know which of the two this method wanted. It did once, and
        reported correctly wired diodes as broken for it.
        """
        direct = self._by_pin.get((ref, pin))
        if direct is not None:
            return direct
        node = self.resolve(f"{ref}.{pin}")
        return self._by_pin.get((node.ref, node.pin)) if node else None

    def resolve(self, spec: str) -> Node | None:
        """Resolve ``"C1.1"`` or ``"U1.VI"`` to a node.

        A bare pin number is matched first; failing that the string is matched against
        the pin *function*, case-insensitively and ignoring KiCad's ``_<n>`` suffix, so
        ``U1.VI`` finds the pin KiCad calls ``VI_3``.
        """
        ref, _, pin = spec.partition(".")
        if not ref or not pin:
            return None
        for node in self.nodes_of(ref):
            if node.pin == pin:
                return node
        wanted = pin.casefold()
        for node in self.nodes_of(ref):
            fn = node.function
            if fn and (fn.casefold() == wanted or _strip_suffix(fn).casefold() == wanted):
                return node
        return None

    def nodes_of(self, ref: str) -> tuple[Node, ...]:
        """Every node belonging to one component, ordered by pin."""
        return self._by_ref.get(ref, ())

    # -- summaries -------------------------------------------------------------

    @property
    def connected_nets(self) -> tuple[Net, ...]:
        """Nets that join two or more pins, ordered by name."""
        return tuple(sorted((n for n in self.nets.values() if n.connected), key=lambda n: n.name))

    @property
    def isolated_nodes(self) -> tuple[Node, ...]:
        """Pins KiCad reports as connected to nothing else."""
        found = (next(iter(net.nodes)) for net in self.nets.values() if len(net.nodes) == 1)
        return tuple(sorted(found))

    @property
    def named_nets(self) -> tuple[Net, ...]:
        return tuple(
            sorted((n for n in self.nets.values() if not n.anonymous), key=lambda n: n.name)
        )

    @property
    def anonymous_nets(self) -> tuple[Net, ...]:
        return tuple(sorted((n for n in self.nets.values() if n.anonymous), key=lambda n: n.name))

    def __str__(self) -> str:
        return (
            f"Netlist({len(self.components)} components, {len(self.nets)} nets, "
            f"{len(self.connected_nets)} connected)"
        )


PinRef = tuple[str, str]
"""A ``(ref, pin_or_function)`` pair as written in a contract."""

Status = Literal["pass", "fail", "unsupported", "skipped"]
"""Check outcome (DECISIONS D9). Only ``pass`` is green.

There is deliberately no fifth value for an indeterminate result: connectivity is
discrete, so a check either resolves or its inputs were unavailable.
"""


def parse_pin_ref(spec: str) -> PinRef:
    """Split ``"C1.1"`` or ``"U1.VI"`` into its parts.

    Raises ``ValueError`` on anything that is not ``<ref>.<pin>``, rather than guessing,
    because a silently mis-parsed contract is the failure this project exists to catch.
    """
    ref, sep, pin = spec.partition(".")
    if not sep or not ref or not pin:
        raise ValueError(f"expected '<ref>.<pin>', got {spec!r}")
    return ref, pin


def _strip_suffix(function: str) -> str:
    """``"VI_3"`` -> ``"VI"``. KiCad suffixes a pin function with its number."""
    head, sep, tail = function.rpartition("_")
    return head if sep and tail.isdigit() else function


def build_netlist(
    nets: Iterable[Net],
    components: Iterable[Component],
    *,
    source: str = "",
    kicad_version: str = "",
) -> Netlist:
    """Assemble a :class:`Netlist`, building its lookup indexes as we go.

    Raises ``ValueError`` if a pin appears on two nets. A netlist *partitions* pins
    into nets -- that is what the structure means, and it is what KiCad emits, shorts
    included: a short merges the two nets and keeps one of the names rather than
    leaving a pin in both. Building the index with a dict comprehension silently let
    one net win by insertion order, which is the wrong answer rather than a slow one.
    """
    net_map = {n.name: n for n in nets}
    by_pin: dict[tuple[str, str], str] = {}
    by_ref: dict[str, list[Node]] = {}
    for net in net_map.values():
        for node in net.nodes:
            key = (node.ref, node.pin)
            if key in by_pin:
                raise ValueError(
                    f"{node.ref}.{node.pin} is on both {by_pin[key]!r} and {net.name!r}. "
                    "A netlist partitions pins into nets, so no pin belongs to two; "
                    "this reading of the design is malformed."
                )
            by_pin[key] = net.name
            by_ref.setdefault(node.ref, []).append(node)
    return Netlist(
        nets=net_map,
        components={c.ref: c for c in components},
        source=source,
        kicad_version=kicad_version,
        _by_pin=by_pin,
        _by_ref={ref: tuple(sorted(found)) for ref, found in by_ref.items()},
    )
