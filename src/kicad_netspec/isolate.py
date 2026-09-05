"""Execute a contract without letting it decide what the board is.

D8 makes a contract *code*, deliberately, and that is not being reversed. What review
established is the consequence: a contract runs **before** the design is read, so
in-process it could replace the oracle and netspec would emit a genuine passing report
about a board the contract invented. No validation of the report can catch that, because
netspec really did produce it.

So the contract runs in a child process and hands back only **declarations**. The parent
reads the netlist itself, from the path the contract named, using its own untouched
oracle. A hostile contract is reduced to lying about its own assertions.

**That residue is deliberate, not overlooked.** Declaring assertions is what a contract
is *for*; a contract that declares weak ones is exactly the thing a report diff is meant
to surface (D8), and no amount of isolation could distinguish a weak assertion from an
honest one. What isolation removes is the ability to lie about the *design*, which
nothing downstream could ever have detected.

One thing it does not remove: a contract names its own ``source``, so it can point at a
different schematic than its reader assumes. That is by design -- and the path it used is
recorded in the report, so it is auditable rather than hidden.

The in-process :func:`kicad_netspec.contract.load` remains, for the pytest plugin: there
you are writing the test, running your own code, in your own process.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from kicad_netspec.contract import Rule, Spec, Unknown

__all__ = ["ContractError", "load_isolated"]

TIMEOUT = 60
"""Seconds. A contract declares; it should not be computing for a minute."""


class ContractError(ValueError):
    """The contract could not be loaded, or did not come back with a usable Spec."""


def _rule_types() -> dict[str, type[Rule]]:
    """Slug to class, from the same subclass set the registry and boundary tests use."""
    return {kind.kind: kind for kind in Rule.__subclasses__() if kind.kind}


def _restore_rule(payload: Any) -> Rule:
    if not isinstance(payload, dict):
        raise ContractError(f"expected a rule object, got {type(payload).__name__}")

    kind = payload.get("kind")
    known = _rule_types()
    if not isinstance(kind, str):
        raise ContractError(f"a rule's kind must be a string, got {type(kind).__name__}")
    if kind not in known:
        # A contract may define its own Rule subclass, which this process never imported.
        # That is a statement about the contract, so it is carried through as a finding
        # rather than raised as an environment fault.
        return Unknown(declared=kind)

    fields = payload.get("fields")
    if not isinstance(fields, dict):
        raise ContractError(f"rule {kind!r} carries no fields")

    # JSON has no tuples, and a Rule is frozen and hashable, so it can hold no list --
    # every sequence that went out as a tuple must come back as one or the rule stops
    # being comparable to the identical rule built in-process.
    restored = {k: tuple(v) if isinstance(v, list) else v for k, v in fields.items()}
    try:
        return known[kind](**restored)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"could not rebuild the {kind!r} rule: {exc}") from exc


def load_isolated(path: str, *, timeout: int = TIMEOUT) -> Spec:
    """Load a contract in a child process and rebuild its Spec here."""
    with tempfile.TemporaryDirectory(prefix="netspec-contract-") as holder:
        result = Path(holder) / "spec.json"
        noise = Path(holder) / "output"
        argv = [sys.executable, "-m", "kicad_netspec._contract_child", path, str(result)]

        # The child's output goes to a FILE, not a pipe. A contract can spawn a detached
        # grandchild, and one inheriting the parent's pipes holds `communicate()` open
        # until it exits -- which let it deterministically rewrite the result after the
        # child had finished. A file is not a channel it can block on.
        try:
            with open(noise, "w+", encoding="utf-8") as sink:
                done = subprocess.run(  # noqa: S603 - argv built here, never caller text
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise ContractError(f"the contract timed out after {timeout}s") from exc
        except OSError as exc:
            raise ContractError(f"could not run the contract: {exc}") from exc

        if done.returncode != 0 or not result.is_file():
            raise ContractError(f"could not load contract: {_explain(noise)}")

        try:
            payload = json.loads(result.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ContractError(f"the contract returned nothing usable: {exc}") from exc

    return _rebuild(payload)


_NOISE_LIMIT = 4000
"""Bytes of a contract's own output to quote back. It is attacker-controlled text on its
way to an agent's context, so it is capped and labelled rather than echoed wholesale."""


def _explain(noise: Path) -> str:
    try:
        text = noise.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:  # pragma: no cover - the file is ours and was just written
        return "no error reported"
    if not text:
        return "no error reported"
    if len(text) > _NOISE_LIMIT:
        text = text[:_NOISE_LIMIT] + f"... [truncated, {len(text)} chars from the contract]"
    return f"the contract said: {text}"


def _text(payload: dict[str, Any], key: str, *, required: bool = True) -> str:
    """A field that reaches a path or an argv must be a string, whatever JSON allowed."""
    value = payload.get(key, "" if not required else None)
    if value is None:
        raise ContractError(f"the contract's Spec is missing {key!r}")
    if not isinstance(value, str):
        raise ContractError(f"{key} must be a string, got {type(value).__name__}")
    return value


def _rebuild(payload: Any) -> Spec:
    if not isinstance(payload, dict):
        raise ContractError("the contract returned no Spec")
    try:
        rules = payload.get("rules", ())
        if not isinstance(rules, list):
            raise ContractError("the contract's rules are not a list")
        variant = payload.get("variant")
        if variant is not None and not isinstance(variant, str):
            raise ContractError("a variant must be a string")
        return Spec(
            source=_text(payload, "source"),
            name=_text(payload, "name", required=False),
            variant=variant,
            require_no_floating_pins=bool(payload.get("require_no_floating_pins", False)),
            rules=[_restore_rule(r) for r in rules],
        )
    except KeyError as exc:
        raise ContractError(f"the contract's Spec is missing {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise ContractError(str(exc)) from exc
