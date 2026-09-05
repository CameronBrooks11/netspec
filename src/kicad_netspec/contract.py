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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

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

    def net_names(self) -> tuple[str, ...]:
        """Every net this rule names, in the words the contract used.

        Resolution (D19) uses this to canonicalise a hierarchical name and to refuse an
        ambiguous one *before* the rule runs. A rule that names nets and does not report
        them here opts out of both, silently, and goes back to comparing raw strings.
        Returning ``()`` is correct only for a rule that names no nets at all.
        """
        return ()


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

    def net_names(self) -> tuple[str, ...]:
        return (self.name,)

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

    def net_names(self) -> tuple[str, ...]:
        return (self.plus, self.minus)

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

    def net_names(self) -> tuple[str, ...]:
        return self.nets

    def __str__(self) -> str:
        return f"{' and '.join(self.nets)} must stay separate"


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
        for rule in self.rules:
            if not isinstance(rule, Rule):
                raise TypeError(f"not a rule: {rule!r}")
        object.__setattr__(self, "rules", tuple(self.rules))

    def __str__(self) -> str:
        return f"Spec({self.name or self.source}, {len(self.rules)} rules)"


# -- constructors, so a contract reads as a list rather than as class instantiation ----


def net(name: str, pins: Iterable[str], *, exact: bool = True) -> Net:
    """Assert which pins are on a net.

    ``exact=False`` asserts only that the listed pins are present, allowing others.
    """
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
