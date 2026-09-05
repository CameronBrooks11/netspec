"""Load one contract, in a process of its own, and hand back only what it declared.

Run as ``python -m kicad_netspec._contract_child <contract> <result-file>``. Never
imported by the parent: that is the entire point.

The result goes to a **file**, not to stdout, so nothing the contract prints -- by
``print``, by ``os.write(1, ...)``, by rebinding ``sys.stdout``, by a subprocess
inheriting the descriptor -- can be mistaken for it. The file is written after the
contract has finished, so a contract that writes there first is simply overwritten.

See :mod:`kicad_netspec.isolate` for what this buys and what it does not.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from typing import Any

__all__ = ["main"]


def _spec_to_json(spec: Any) -> dict[str, Any]:
    return {
        "source": spec.source,
        "name": spec.name,
        "variant": spec.variant,
        "require_no_floating_pins": spec.require_no_floating_pins,
        "rules": [{"kind": r.kind, "fields": dataclasses.asdict(r)} for r in spec.rules],
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:  # pragma: no cover - the parent always passes two
        print("usage: _contract_child <contract> <result-file>", file=sys.stderr)
        return 2

    target, result_file = argv
    from kicad_netspec import contract

    try:
        spec = contract.load(target)
    except BaseException as exc:  # noqa: BLE001 - including SystemExit; a contract
        # declares, it does not decide. Anything it raises is reported, never obeyed.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    with open(result_file, "w", encoding="utf-8") as handle:
        json.dump(_spec_to_json(spec), handle)
        handle.flush()
        os.fsync(handle.fileno())

    # os._exit, not return: a contract can register an atexit handler or leave a
    # non-daemon thread, both of which run after main() and were able to rewrite the
    # result the parent is about to read. Nothing of the contract's runs past here.
    os._exit(0)


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main(sys.argv[1:]))
