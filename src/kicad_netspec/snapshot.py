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

SNAPSHOT_SCHEMA = 2
"""2 added a net's class and a component's sheet. A schema-1 snapshot still reads;
both fields are simply absent, which is what an empty value means everywhere else."""
"""Bumped only when the on-disk shape changes incompatibly."""


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
                "sheet": c.sheet,
            }
            for c in sorted(netlist.components.values(), key=lambda c: c.ref)
        ],
        "nets": [
            {
                "name": net.name,
                "netclass": net.netclass,
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
            netclass=net.get("netclass", ""),
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
            sheet=c.get("sheet", ""),
        )
        for c in payload.get("components", ())
    ]
    return build_netlist(
        nets,
        components,
        source=payload.get("source", ""),
        kicad_version=payload.get("kicad_version", ""),
    )
