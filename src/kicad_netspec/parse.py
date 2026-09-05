"""Turn KiCad's ``kicadxml`` netlist into the canonical model.

Stdlib only. ``kicadxml`` is chosen over ``kicadsexpr`` because it parses with
``xml.etree`` and carries two attributes an s-expression netlist does not surface as
cleanly: ``pinfunction`` and ``pintype`` (DECISIONS D12).

This module reads a *netlist* -- a document KiCad produced. It never reads a
``.kicad_sch`` or ``.kicad_pcb``; that is KiCad's job, reached through the oracle.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from kicad_netspec.model import Component, Net, Netlist, Node, build_netlist

__all__ = ["ParseError", "parse_kicadxml", "parse_kicadxml_file"]


class ParseError(ValueError):
    """The document was not a KiCad XML netlist, or was malformed."""


def parse_kicadxml_file(path: str | Path) -> Netlist:
    """Parse a ``kicadxml`` netlist from disk."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - surfaced as an environment fault upstream
        raise ParseError(f"cannot read {p}: {exc}") from exc
    return parse_kicadxml(text, source=str(p))


def parse_kicadxml(text: str, *, source: str = "") -> Netlist:
    """Parse a ``kicadxml`` netlist from a string."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ParseError(f"not valid XML: {exc}") from exc

    if root.tag != "export":
        raise ParseError(f"expected a KiCad <export> netlist, found <{root.tag}>")

    try:
        return build_netlist(
            _nets(root),
            _components(root),
            source=source or _source(root),
            kicad_version=_kicad_version(root),
        )
    except ValueError as exc:
        # The model enforces invariants a netlist must hold (a pin belongs to one net).
        # A document that breaks one is malformed, and that is an environment fault --
        # "I could not read this" -- not a finding about the design (D10). Raising it as
        # ParseError is what lets the oracle report it as one instead of a traceback.
        raise ParseError(f"malformed netlist: {exc}") from exc


def _nets(root: ET.Element) -> list[Net]:
    container = root.find("nets")
    if container is None:
        return []
    out: list[Net] = []
    for element in container.findall("net"):
        name = element.get("name")
        if name is None:
            # A net with no name cannot be addressed by a contract, and silently
            # dropping it would understate connectivity. Refuse instead.
            raise ParseError(f"net {element.get('code', '?')} has no name attribute")
        nodes = frozenset(
            Node(
                ref=node.get("ref", ""),
                pin=node.get("pin", ""),
                function=node.get("pinfunction"),
                type=node.get("pintype"),
            )
            for node in element.findall("node")
        )
        # KiCad joins a net's classes with "," and does not escape them; see
        # Net.netclasses for what that costs. Split without stripping.
        raw_classes = element.get("class", "")
        out.append(
            Net(
                name=name,
                nodes=nodes,
                netclasses=tuple(raw_classes.split(",")) if raw_classes else (),
            )
        )
    return out


def _components(root: ET.Element) -> list[Component]:
    container = root.find("components")
    if container is None:
        return []
    out: list[Component] = []
    for element in container.findall("comp"):
        ref = element.get("ref")
        if ref is None:
            raise ParseError("component entry has no ref attribute")
        libsource = element.find("libsource")
        lib_id = None
        if libsource is not None:
            lib = libsource.get("lib")
            part = libsource.get("part")
            lib_id = f"{lib}:{part}" if lib and part else part or lib
        # <sheetpath> also carries tstamps="/<uuid>/". Only the name path is taken:
        # a UUID is exactly the kind of identifier D11 keeps out of this model.
        sheetpath = element.find("sheetpath")
        sheet = sheetpath.get("names", "") if sheetpath is not None else ""
        out.append(
            Component(
                ref=ref,
                value=(element.findtext("value") or "").strip(),
                footprint=(element.findtext("footprint") or "").strip() or None,
                lib_id=lib_id,
                attributed_sheet=sheet,
            )
        )
    return out


def _source(root: ET.Element) -> str:
    design = root.find("design")
    return (design.findtext("source") or "").strip() if design is not None else ""


def _kicad_version(root: ET.Element) -> str:
    design = root.find("design")
    return (design.findtext("tool") or "").strip() if design is not None else ""
