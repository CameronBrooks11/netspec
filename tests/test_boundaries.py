"""Structural rules that keep netspec migratable (docs/DECISIONS.md D3, D4)."""

from __future__ import annotations

import ast
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


# Modules permitted to spawn a process for reasons unrelated to KiCad (D4.1).
# Adding to this list is a design change; argue for it in docs/DECISIONS.md first.
SPAWN_EXEMPT = {"ops/run.py"}


def _in_oracle(path: Path) -> bool:
    return "oracle" in path.relative_to(SRC).parts


def test_kicad_is_only_invoked_from_the_oracle() -> None:
    """No KiCad binary is named outside oracle/, so the IPC backend stays one file."""
    offenders = [
        p.relative_to(SRC)
        for p in _py_files()
        if not _in_oracle(p) and "kicad-cli" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"KiCad must only be reachable through the Oracle protocol; found in {offenders}"
    )


def test_only_the_oracle_and_the_command_runner_spawn_processes() -> None:
    """Everything else works on the model and touches no process at all (D4.1)."""
    offenders = []
    for p in _py_files():
        rel = p.relative_to(SRC)
        if _in_oracle(p) or rel.as_posix() in SPAWN_EXEMPT:
            continue
        if "subprocess" in p.read_text(encoding="utf-8"):
            offenders.append(rel)
    assert not offenders, f"unexpected process spawning outside the oracle: {offenders}"


def test_the_command_runner_never_invokes_kicad() -> None:
    """`guard` runs whatever the user names. That must never be a KiCad binary.

    Checked against what the module actually references, not against the word "kicad" --
    the docstring is allowed to explain the rule it is subject to.
    """
    runner = SRC / "ops" / "run.py"
    if not runner.exists():
        return

    tree = ast.parse(runner.read_text(encoding="utf-8"))
    referenced = {
        node.value.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    # Docstrings are constants too, so look only for something that could be executed.
    invocations = {s for s in referenced if "kicad-cli" in s or "flatpak" in s}
    assert not invocations, f"ops/run.py must not invoke KiCad: {invocations}"

    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any("oracle" in name for name in imports), (
        "ops/run.py must not reach into the oracle"
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
