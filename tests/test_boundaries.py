"""Structural rules that keep netspec migratable (docs/DECISIONS.md D3, D4)."""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "kicad_netspec"


def _py_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_never_imports_pcbnew() -> None:
    """SWIG pcbnew is already deleted in KiCad master. Nothing may depend on it."""
    offenders = [
        p.relative_to(SRC) for p in _py_files() if "import pcbnew" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"pcbnew has no future in KiCad 11: {offenders}"


def test_kicad_only_leaks_in_through_the_oracle() -> None:
    """`kicad-cli` and subprocess live in oracle/ only, so the IPC backend is one file."""
    offenders = []
    for p in _py_files():
        if p.parent.name == "oracle" or p.parent.parent.name == "oracle":
            continue
        text = p.read_text(encoding="utf-8")
        if "kicad-cli" in text or "subprocess" in text:
            offenders.append(p.relative_to(SRC))
    assert not offenders, (
        f"KiCad must only be reachable through the Oracle protocol; found in {offenders}"
    )


def test_report_carries_no_tolerance() -> None:
    """Guard against cargo-culting partspec's interval model.

    Connectivity is discrete: two pins are connected or they are not. If a tolerance
    concept appears in the model, partspec's epistemics have leaked in (design/audit).
    """
    banned = ("tolerance", "approximate", "interval")
    offenders = []
    for p in _py_files():
        lowered = p.read_text(encoding="utf-8").lower()
        for word in banned:
            if word in lowered:
                offenders.append((p.relative_to(SRC), word))
    assert not offenders, f"netspec has no tolerances; connectivity is exact: {offenders}"
