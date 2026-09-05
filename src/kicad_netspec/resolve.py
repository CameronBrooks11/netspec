"""Match the net names a contract uses to the nets the design actually has.

A contract is written by a person, against a design that names its nets in full. On a
hierarchical board KiCad calls a net ``/Motor Driver 1/VS``; a contract that says ``VS``
is abbreviated rather than wrong. Resolving that abbreviation is the difference between
netspec working on a hierarchical board and not working on one at all.

Resolution runs **before** any rule is evaluated, and has three outcomes:

===========  ===========================================================================
exact        the design has a net by exactly that name
leaf         the design has exactly one net whose last path segment matches
ambiguous    more than one net matches, and there is no correct guess
===========  ===========================================================================

The middle outcome is why this is a phase rather than a lookup helper, and the third is
why it cannot be a lookup helper. On a dual-channel board ``OUT`` matches both channels.
Picking one would invent intent the contract never expressed, so resolution refuses and
says which names it could have meant. That is a defect in the contract, reported against
the contract -- not a finding about the board.

Only net names are resolved. Components and pins are named exactly, are unique across a
design, and have no abbreviated form, so there is nothing to resolve; the rules look
those up directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from kicad_netspec.contract import Forbid, Net, Polarity, Rule, Spec
from kicad_netspec.model import Netlist

__all__ = ["Resolution", "Resolved", "find_nets", "net_names_of", "resolve_spec"]


def find_nets(netlist: Netlist, name: str) -> tuple[str, ...]:
    """Every net the contract's ``name`` could mean, in a stable order.

    An exact match wins outright and is never joined by leaf matches: a design carrying
    both ``VIN`` and ``/Sheet/VIN`` resolves ``VIN`` to the root net, because that is
    what the author wrote.
    """
    if name in netlist.nets:
        return (name,)
    return tuple(sorted(n for n in netlist.nets if n.rpartition("/")[2] == name))


@dataclass(frozen=True)
class Resolution:
    """One name a contract used, and what the design turned out to call it."""

    name: str
    candidates: tuple[str, ...] = ()

    @property
    def target(self) -> str | None:
        """The one net this name means, or None when that is not a single answer."""
        return self.candidates[0] if len(self.candidates) == 1 else None

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1

    @property
    def absent(self) -> bool:
        return not self.candidates

    @property
    def problem(self) -> str:
        """Why this name cannot be used, or empty when it can."""
        if not self.ambiguous:
            return ""
        return (
            f"{self.name!r} matches {len(self.candidates)} nets "
            f"({', '.join(self.candidates)}) -- say which one you mean"
        )


@dataclass(frozen=True)
class Resolved:
    """Every net name a contract used, resolved against one reading of the design."""

    by_name: Mapping[str, Resolution] = field(default_factory=dict)

    def net(self, name: str) -> str | None:
        """The design's own name for ``name``, or None if it is absent or ambiguous."""
        found = self.by_name.get(name)
        return found.target if found else None

    def problems_for(self, rule: Rule) -> tuple[str, ...]:
        """Reasons this rule cannot be evaluated as written."""
        seen = (self.by_name.get(n) for n in net_names_of(rule))
        return tuple(r.problem for r in seen if r is not None and r.problem)


def net_names_of(rule: Rule) -> tuple[str, ...]:
    """Every net a rule names, in the words the contract used."""
    if isinstance(rule, Net):
        return (rule.name,)
    if isinstance(rule, Polarity):
        return (rule.plus, rule.minus)
    if isinstance(rule, Forbid):
        return rule.nets
    return ()


def resolve_spec(spec: Spec, netlist: Netlist) -> Resolved:
    """Resolve every net name in ``spec`` against ``netlist``, once."""
    names = {name for rule in spec.rules for name in net_names_of(rule)}
    return Resolved(
        by_name={name: Resolution(name, find_nets(netlist, name)) for name in sorted(names)}
    )
