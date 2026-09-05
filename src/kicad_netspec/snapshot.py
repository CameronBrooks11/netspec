"""Persist a netlist as stable JSON.

Two properties matter and both are deliberate:

* **Stable.** Keys sorted, sets serialised in a fixed order, no coordinates, no UUIDs, no
  timestamps. Re-snapshotting an unchanged design produces a byte-identical file, so a
  snapshot can be committed to git and a spurious diff means something really moved.
* **Self-describing.** A schema version travels with the data, because a snapshot is
  expected to outlive the version of netspec that wrote it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kicad_netspec.model import Component, Net, Netlist, Node, build_netlist

__all__ = ["SNAPSHOT_SCHEMA", "SnapshotError", "dumps", "loads", "read", "write"]

SNAPSHOT_SCHEMA = 1
"""Bumped only when the on-disk shape changes incompatibly.

Note what is *not* here: a net's class and a component's sheet. A snapshot holds what
the diff compares, and the diff compares membership. Persisting a fact the diff ignores
would put a repo's committed snapshot in conflict with netspec's own verdict -- a red
`git diff` beside a green `netspec diff`, with nothing naming the cause -- which is
exactly the "spurious diff" this module's docstring promises cannot happen.
"""


class SnapshotError(ValueError):
    """The file was not a netspec snapshot, or was written by a newer schema."""


def dumps(netlist: Netlist, *, indent: int | None = 2) -> str:
    """Serialise a netlist to stable JSON text."""
    return json.dumps(_to_json(netlist), indent=indent, sort_keys=True) + "\n"


def loads(text: str) -> Netlist:
    """Parse a snapshot back into a netlist."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"not valid JSON: {exc}") from exc
    return _from_json(payload)


def write(netlist: Netlist, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(netlist), encoding="utf-8")
    return out


def read(path: str | Path) -> Netlist:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise SnapshotError(f"cannot read {p}: {exc}") from exc
    return loads(text)


def _to_json(netlist: Netlist) -> dict[str, Any]:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "source": netlist.source,
        "kicad_version": netlist.kicad_version,
        "components": [
            {
                "ref": c.ref,
                "value": c.value,
                "footprint": c.footprint,
                "lib_id": c.lib_id,
            }
            for c in sorted(netlist.components.values(), key=lambda c: c.ref)
        ],
        "nets": [
            {
                "name": net.name,
                "nodes": [
                    {
                        "ref": n.ref,
                        "pin": n.pin,
                        "function": n.function,
                        "type": n.type,
                    }
                    for n in sorted(net.nodes)
                ],
            }
            for net in sorted(netlist.nets.values(), key=lambda n: n.name)
        ],
    }


def _from_json(payload: Any) -> Netlist:
    if not isinstance(payload, dict):
        raise SnapshotError("expected a JSON object")

    # A check report is also JSON, also carries a `schema`, and also lives in a .json
    # file. Loaded as a snapshot it yields no nets, so `netspec diff` on two reports said
    # "no change in connectivity" and exited 0 -- a confident answer to a question nobody
    # asked, in the exact situation where someone is looking for a weakened assertion.
    if "command" in payload:
        raise SnapshotError(
            f"this is a netspec {payload['command']!r} report, not a snapshot. "
            "`diff` compares connectivity; it cannot compare reports."
        )

    schema = payload.get("schema")
    if schema is None:
        raise SnapshotError("not a netspec snapshot (no schema field)")
    if not isinstance(schema, int) or schema > SNAPSHOT_SCHEMA:
        raise SnapshotError(
            f"snapshot schema {schema} is newer than this netspec understands "
            f"({SNAPSHOT_SCHEMA}); upgrade netspec"
        )

    nets = [
        Net(
            name=net["name"],
            nodes=frozenset(
                Node(
                    ref=n["ref"],
                    pin=n["pin"],
                    function=n.get("function"),
                    type=n.get("type"),
                )
                for n in net.get("nodes", ())
            ),
        )
        for net in payload.get("nets", ())
    ]
    components = [
        Component(
            ref=c["ref"],
            value=c.get("value", ""),
            footprint=c.get("footprint"),
            lib_id=c.get("lib_id"),
        )
        for c in payload.get("components", ())
    ]
    return build_netlist(
        nets,
        components,
        source=payload.get("source", ""),
        kicad_version=payload.get("kicad_version", ""),
    )
