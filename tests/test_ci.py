"""The Action's engine: rendering and output plumbing, without GitHub or KiCad."""

from __future__ import annotations

import os
from pathlib import Path

from kicad_netspec.check import check_spec
from kicad_netspec.ci import (
    MARKER,
    _emit,
    _env_failure,
    _paths,
    _render_check,
    _render_diff,
    render,
)
from kicad_netspec.contract import Spec, polarity
from kicad_netspec.diff import diff_netlists
from kicad_netspec.model import Component, Net, Node, build_netlist


def _rail(plus_on: str = "VIN", minus_on: str = "GND"):
    nets = {"VIN": [Node("R1", "1")], "GND": [Node("R1", "2")]}
    nets[plus_on].append(Node("C1", "1"))
    nets[minus_on].append(Node("C1", "2"))
    return build_netlist(
        [Net(name=k, nodes=frozenset(v)) for k, v in nets.items()],
        [Component("C1"), Component("R1")],
    )


def test_paths_accepts_newlines_and_commas() -> None:
    assert _paths("a.kicad_sch\nb.kicad_sch") == [Path("a.kicad_sch"), Path("b.kicad_sch")]
    assert _paths("a.kicad_sch, b.kicad_sch") == [Path("a.kicad_sch"), Path("b.kicad_sch")]
    assert _paths("  \n ") == []


def test_a_pin_swap_renders_a_warning_callout() -> None:
    result = diff_netlists(_rail(), _rail("GND", "VIN"))
    body = _render_diff(result)
    assert "[!WARNING]" in body
    assert "ERC has no rule against" in body
    assert "C1 on" in body


def test_an_ordinary_change_renders_without_the_warning() -> None:
    after = build_netlist(
        [
            Net("VIN", frozenset({Node("R1", "1"), Node("C1", "1"), Node("C7", "1")})),
            Net("GND", frozenset({Node("R1", "2"), Node("C1", "2")})),
        ],
        [Component("C1"), Component("R1"), Component("C7")],
    )
    body = _render_diff(diff_netlists(_rail(), after))
    assert "[!WARNING]" not in body
    assert "Net changes" in body


def test_check_renders_a_table_with_escaped_pipes() -> None:
    spec = Spec(source="x", rules=[polarity("C1", plus="VIN", minus="GND")])
    body = _render_check(check_spec(spec, _rail("GND", "VIN")))
    assert "| ❌ |" in body
    assert "**FAIL**" in body
    assert "\n|" in body


def test_the_headline_reflects_severity() -> None:
    assert "looks like a defect" in render([], changed=True, suspicious=True)
    assert render([], changed=True, suspicious=False).count("looks like a defect") == 0
    assert "unchanged" in render([], changed=False, suspicious=False)


def test_every_report_carries_the_marker_so_comments_update_in_place() -> None:
    assert render([], changed=False, suspicious=False).startswith(MARKER)
    assert _env_failure("boom").startswith(MARKER)


def test_a_missing_engine_says_it_examined_nothing() -> None:
    """DECISIONS D10: no KiCad is not a verdict on the board."""
    body = _env_failure("kicad-cli not found")
    assert "has **not** examined this design" in body
    assert "says nothing about the board" in body


def test_emit_writes_a_delimited_multiline_output(tmp_path: Path) -> None:
    out = tmp_path / "gh-output"
    os.environ["GITHUB_OUTPUT"] = str(out)
    try:
        _emit(report="line one\nline two", changed=True, suspicious=False)
    finally:
        del os.environ["GITHUB_OUTPUT"]

    written = out.read_text()
    assert "changed=true" in written
    assert "suspicious=false" in written
    assert "report<<NETSPEC_EOF" in written
    assert written.rstrip().endswith("NETSPEC_EOF")


def test_emit_is_a_no_op_outside_actions() -> None:
    os.environ.pop("GITHUB_OUTPUT", None)
    _emit(report="x", changed=False, suspicious=False)  # must not raise
