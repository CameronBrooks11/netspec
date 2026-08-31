"""Pin the KiCad API facts this project plans around.

netspec ships on KiCad 10 via ``kicad-cli``. A future IPC backend (DECISIONS D8) will
use commands that exist in KiCad master *today* -- verified by reading the source, not a
roadmap. KiCad's public roadmap wiki is stale and must not be used for planning.

This test re-verifies those facts on demand::

    pytest --kicad-source=~/src/kicad

CI runs it weekly against KiCad master, so an upstream revert reaches us as a red build
rather than a surprise when KiCad 11 ships.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Commands a future Ipc11Backend depends on, per handler file.
# Verified present in kicad master @ 379565b7 (2026-08-30).
REQUIRED: dict[str, frozenset[str]] = {
    "eeschema/api/api_handler_sch.cpp": frozenset(
        {
            "GetItems",
            "GetItemsById",
            "GetOpenDocuments",
            "GetSchematicNetlist",  # the whole reason for the IPC backend
            "GetSchematicHierarchy",
            "SaveDocument",
            "RunSchematicJobExportNetlist",
            "RunSchematicJobExportBOM",
        }
    ),
    "pcbnew/api/api_handler_pcb.cpp": frozenset(
        {
            "GetItems",
            "GetNets",
            "GetOpenDocuments",
            "ImportNetlist",  # headless schematic -> PCB; absent in KiCad 10
            "SaveDocument",
            "GetBoardDesignRules",
        }
    ),
    "common/api/api_handler_common.cpp": frozenset(
        {"GetVersion", "Ping", "OpenDocument", "CloseDocument"}
    ),
}

# Facts about the shape of KiCad 11 that the design leans on.
HEADLESS_MARKERS = {
    "kicad/cli/command_api_server.cpp": "api-server",
    "pcbnew/api/headless_pcb_context.cpp": "HEADLESS_PCB_CONTEXT",
}

_REGISTER = re.compile(r"registerHandler<\s*(?:commands::)?([A-Za-z]+)")


def _registered(path: Path) -> set[str]:
    return set(_REGISTER.findall(path.read_text(encoding="utf-8", errors="ignore")))


@pytest.mark.parametrize("rel", sorted(REQUIRED))
def test_planned_api_commands_still_exist(kicad_source: Path, rel: str) -> None:
    src = kicad_source / rel
    assert src.is_file(), f"{rel} has moved or been removed upstream"
    missing = REQUIRED[rel] - _registered(src)
    assert not missing, (
        f"{rel} no longer registers {sorted(missing)}. "
        "A KiCad API command netspec plans to use has disappeared upstream -- "
        "revisit docs/DECISIONS.md D8 before relying on it."
    )


@pytest.mark.parametrize("rel,marker", sorted(HEADLESS_MARKERS.items()))
def test_headless_mode_still_exists(kicad_source: Path, rel: str, marker: str) -> None:
    src = kicad_source / rel
    assert src.is_file(), f"{rel} is gone; headless KiCad 11 may no longer be planned"
    assert marker in src.read_text(encoding="utf-8", errors="ignore")


def test_swig_is_still_gone(kicad_source: Path) -> None:
    """SWIG pcbnew is deleted in master. If it returns, D3 is worth revisiting."""
    assert not (kicad_source / "pcbnew" / "python" / "swig").exists()
    assert not list(kicad_source.glob("pcbnew/python/**/*.i"))
