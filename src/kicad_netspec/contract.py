"""Declared intent: what the design is *supposed* to be.

A contract is Python (DECISIONS D8), not a sidecar data file. It is more expressive, it
needs no schema to design or version, and it avoids the well-documented trap of inventing
a policy DSL.

The consequence is that ``netspec check`` **imports and executes** the module you name.
A contract is code. ``netspec diff`` executes nothing.

Pins are named by function wherever the symbol offers one -- ``U1.VI`` rather than
``U1.3`` (DECISIONS D12). Pin *numbers* are what the known schematic-writer bugs corrupt,
so an assertion written against a number can be satisfied by the very defect it was meant
to catch.

    from kicad_netspec import Spec, net, polarity, forbid

    board = Spec(
        source="hardware/board.kicad_sch",
        rules=[
            net("VIN",  ["J1.1", "C1.1", "U1.VI"]),
            net("+3V3", ["U1.VO", "C2.1"]),
            polarity("C1", plus="VIN", minus="GND"),
            forbid("VIN", "GND"),
        ],
    )
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

__all__ = [
    "Forbid",
    "Net",
    "Polarity",
    "Rule",
    "Spec",
    "forbid",
    "net",
    "polarity",
]


class Rule:
    """One assertion in a contract.

    Rules are **pure data**. A rule declares what must be true and knows nothing about
    netlists or about how it is checked; adjudication lives in
    :mod:`kicad_netspec.check`, which registers one checker per rule type. That split is
    load-bearing: a contract module is user code that ``netspec check`` executes, and it
    has no business importing the machinery that judges it.

    Adding a primitive is a frozen dataclass inheriting this, a constructor function
    below, and a ``@checks`` handler in ``check.py``. Two tests in ``test_boundaries``
    fail if either half is missing.
    """

    kind: ClassVar[str] = ""
    """Stable slug naming this rule type in the machine-readable report.

    Deliberately not the class name: a report is a persisted artifact (D2) and a rename
    in here must not silently re-key someone's stored report.
    """

    def net_names(self) -> tuple[str, ...]:
        """Every net this rule names, in the words the contract used.

        Resolution (D19) uses this to canonicalise a hierarchical name and to refuse an
        ambiguous one *before* the rule runs. A rule that names nets and does not report
        them here opts out of both, silently, and goes back to comparing raw strings.
        Returning ``()`` is correct only for a rule that names no nets at all.
        """
        return ()

    def describe(self) -> dict[str, Any]:
        """This rule as JSON-safe data, for the report.

        Must include ``subject``: the one thing the rule is about, which together with
        ``kind`` identifies the assertion across runs. That identity is what lets two
        reports be aligned so a *removed* assertion is distinguishable from an edited
        one -- the objection D8 answers. Everything else in the returned mapping is the
        rule's strength, and is expected to change when someone weakens it.

        No tuples, no frozensets: this is serialised verbatim.
        """
        return {}


@dataclass(frozen=True)
class Net(Rule):
    """These pins, and by default *only* these pins, are on this net."""

    name: str
    pins: tuple[str, ...]
    exact: bool = True
    """When True, an unexpected extra pin on the net is a failure.

    Exact by default: a contract that only checks for presence cannot notice a stray
    connection, and a stray connection is a short.
    """

    kind: ClassVar[str] = "net"

    def __post_init__(self) -> None:
        # On the dataclass, not only in net(): Net is exported, so validating in the
        # helper alone left `Net(name="VIN", pins=())` as a way in.
        if not self.name:
            raise ValueError("a net rule needs a net name")
        if not self.pins:
            raise ValueError(f"net {self.name!r} lists no pins, so it asserts nothing")

    def net_names(self) -> tuple[str, ...]:
        return (self.name,)

    def describe(self) -> dict[str, Any]:
        return {"subject": self.name, "pins": list(self.pins), "exact": self.exact}

    def __str__(self) -> str:
        kind = "exactly" if self.exact else "at least"
        return f"net {self.name} carries {kind} {', '.join(self.pins)}"


@dataclass(frozen=True)
class Polarity(Rule):
    """A polarised part is the right way round.

    Reversing an electrolytic capacitor, a diode or an LED is *legal wiring*: ERC has no
    rule against it and the netlist looks healthy. This is the assertion that catches it.
    """

    ref: str
    plus: str
    minus: str
    plus_pin: str = "1"
    minus_pin: str = "2"
    """KiCad's convention for two-pin polarised symbols. Override for parts that differ."""

    kind: ClassVar[str] = "polarity"

    def net_names(self) -> tuple[str, ...]:
        return (self.plus, self.minus)

    def describe(self) -> dict[str, Any]:
        return {
            "subject": self.ref,
            "plus": self.plus,
            "minus": self.minus,
            "plus_pin": self.plus_pin,
            "minus_pin": self.minus_pin,
        }

    def __str__(self) -> str:
        return (
            f"{self.ref} pin {self.plus_pin} on {self.plus}, pin {self.minus_pin} on {self.minus}"
        )


@dataclass(frozen=True)
class Forbid(Rule):
    """These nets must never be the same net.

    The assertion for a short that would otherwise read as a perfectly ordinary net.
    """

    nets: tuple[str, ...]

    kind: ClassVar[str] = "forbid"

    def net_names(self) -> tuple[str, ...]:
        return self.nets

    def describe(self) -> dict[str, Any]:
        # Sorted throughout, not just in the subject: forbid(A, B) and forbid(B, A) are
        # one assertion. Canonicalising the key but leaving the body unsorted made the
        # two align and then report a change, which cancels the benefit exactly.
        # JSON-encoded rather than joined on a separator: KiCad accepts "|" (and ":")
        # inside a net name, so `forbid("A|B", "C")` and `forbid("A", "B|C")` joined to
        # the same string and keyed to one id -- two different assertions, one key.
        ordered = sorted(self.nets)
        return {"subject": json.dumps(ordered), "nets": ordered}

    def __str__(self) -> str:
        return f"{' and '.join(sorted(self.nets))} must stay separate"


def _merge(first: Rule, second: Rule) -> Rule | None:
    """Combine two rules about one subject, or None when they genuinely conflict.

    Two ``exact=False`` net rules compose by union -- "at least A" and "at least B" is
    "at least A, B" -- and refusing them would break a real shape: a contract assembled
    from per-subsystem rule lists, where two independently authored blocks each name
    their own pins on a shared rail and neither can know the other's. Anything else is a
    contradiction, and the id can only be a key if one subject means one assertion.
    """
    if isinstance(first, Net) and isinstance(second, Net) and not (first.exact or second.exact):
        return Net(
            name=first.name,
            pins=first.pins + tuple(p for p in second.pins if p not in first.pins),
            exact=False,
        )
    return None


@dataclass(frozen=True)
class Spec:
    """One design and everything asserted about it."""

    source: str
    """Path to the schematic, relative to the contract file or absolute."""

    rules: Sequence[Rule] = ()
    """Any sequence; normalised to a tuple so a Spec stays hashable.

    Declared as a Sequence because a contract is written by hand and a list literal is
    the natural way to write one. Requiring a trailing comma to make a tuple would be a
    tax on the thing this project most wants people to write.
    """

    name: str = ""
    variant: str | None = None

    require_no_floating_pins: bool = False
    """Fail if any pin is connected to nothing.

    Off by default: real designs legitimately leave pins unconnected, and KiCad's own
    no-connect flags are the right way to declare that. Turn it on for a board where you
    have marked every intentional one.
    """

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("a Spec needs a source schematic")
        seen: dict[tuple[str, str], Rule] = {}
        for rule in self.rules:
            if not isinstance(rule, Rule):
                raise TypeError(f"not a rule: {rule!r}")
            # The report keys a result by kind:subject so two runs can be aligned and a
            # deleted assertion told from a weakened one (D23). Two rules sharing that
            # key destroy the property -- and worse, they are how an agent could smuggle
            # a weak assertion in beside a strong one, since the obvious id-keyed
            # comparator keeps only the last. Refuse rather than let the guarantee rot.
            if rule.kind == "spec":
                raise ValueError(
                    f"{type(rule).__name__} claims kind 'spec', which netspec reserves "
                    "for its own Spec-level findings"
                )
            # Normalised exactly as the report normalises it, so "" and "unknown" cannot
            # be two keys here and one there.
            key = (rule.kind or "unknown", str(rule.describe().get("subject", "")))
            if key in seen:
                merged = _merge(seen[key], rule)
                if merged is None:
                    raise ValueError(
                        f"two {key[0]} rules about {key[1]!r}: {seen[key]} / {rule}. "
                        "A contract states one thing per subject."
                    )
                seen[key] = merged
                continue
            seen[key] = rule
        object.__setattr__(self, "rules", tuple(seen.values()))

    def __str__(self) -> str:
        return f"Spec({self.name or self.source}, {len(self.rules)} rules)"


# -- constructors, so a contract reads as a list rather than as class instantiation ----


def net(name: str, pins: Iterable[str], *, exact: bool = True) -> Net:
    """Assert which pins are on a net.

    ``exact=False`` asserts only that the listed pins are present, allowing others.
    """
    # "carries at least nothing" is true of every net and "carries exactly nothing" of
    # none, so a rule with no pins cannot discriminate; Net.__post_init__ refuses both.
    return Net(name=name, pins=tuple(pins), exact=exact)


def polarity(
    ref: str, *, plus: str, minus: str, plus_pin: str = "1", minus_pin: str = "2"
) -> Polarity:
    """Assert a polarised part is the right way round."""
    return Polarity(ref=ref, plus=plus, minus=minus, plus_pin=plus_pin, minus_pin=minus_pin)


def forbid(*nets: str) -> Forbid:
    """Assert two or more nets never merge."""
    if len(nets) < 2:
        raise ValueError("forbid() needs at least two nets")
    return Forbid(nets=tuple(nets))


def load(path: str, *, attribute: str | None = None) -> Spec:
    """Import a contract module and return the :class:`Spec` it defines.

    ``path`` may be ``contract.py`` or ``contract.py:name``. With no name, the module
    must define exactly one ``Spec`` at module level, so there is nothing to guess about.

    This executes the module. See the note at the top of this file.
    """
    import importlib.util
    from pathlib import Path

    target, _, named = path.partition(":")
    attribute = attribute or named or None

    file = Path(target).expanduser().resolve()
    if not file.is_file():
        raise FileNotFoundError(f"no contract at {file}")

    spec = importlib.util.spec_from_file_location(file.stem, file)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if attribute:
        found = getattr(module, attribute, None)
        if not isinstance(found, Spec):
            raise ValueError(f"{file}:{attribute} is not a Spec")
        return found

    specs = [v for k, v in vars(module).items() if isinstance(v, Spec) and not k.startswith("_")]
    if not specs:
        raise ValueError(f"{file} defines no Spec")
    if len(specs) > 1:
        raise ValueError(f"{file} defines {len(specs)} Specs; name one, e.g. {file.name}:board")
    return specs[0]


def resolve_source(spec: Spec, contract_path: str | None = None) -> str:
    """Turn a Spec's ``source`` into a usable path, relative to the contract file."""
    from pathlib import Path

    source = Path(spec.source).expanduser()
    if source.is_absolute() or contract_path is None:
        return str(source)
    base = Path(contract_path.partition(":")[0]).expanduser().resolve().parent
    return str((base / source).resolve())
