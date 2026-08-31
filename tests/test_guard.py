"""`guard` composes with anything, so it is tested against a fake editor."""

from __future__ import annotations

from pathlib import Path

from kicad_netspec.contract import Spec, polarity
from kicad_netspec.model import Component, Net, Node, build_netlist
from kicad_netspec.ops.guard import guard
from kicad_netspec.ops.run import run_command


def _rail(plus_on: str, minus_on: str):
    nets = {"VIN": [Node("R1", "1")], "GND": [Node("R1", "2")]}
    nets[plus_on].append(Node("C1", "1"))
    nets[minus_on].append(Node("C1", "2"))
    return build_netlist(
        [Net(name=k, nodes=frozenset(v)) for k, v in nets.items()],
        [Component("C1"), Component("R1")],
    )


class _FakeDesign:
    """Stands in for an oracle: returns a different netlist after the command runs."""

    def __init__(self, before, after) -> None:
        self._readings = [before, after]

    def __call__(self, _path: Path):
        return self._readings.pop(0) if len(self._readings) > 1 else self._readings[0]


def test_guard_is_green_when_nothing_meaningful_changed(tmp_path: Path) -> None:
    same = _rail("VIN", "GND")
    result = guard(tmp_path / "b.kicad_sch", ["true"], read=_FakeDesign(same, same))
    assert result.ok
    assert result.diff.empty


def test_guard_fails_when_the_command_reverses_a_part(tmp_path: Path) -> None:
    read = _FakeDesign(_rail("VIN", "GND"), _rail("GND", "VIN"))
    result = guard(tmp_path / "b.kicad_sch", ["true"], read=read)

    assert not result.ok
    assert result.command.ok, "the command itself succeeded -- that is the point"
    assert {s.ref for s in result.diff.pin_swaps} == {"C1"}


def test_guard_fails_when_the_contract_is_violated(tmp_path: Path) -> None:
    read = _FakeDesign(_rail("VIN", "GND"), _rail("GND", "VIN"))
    spec = Spec(source="b.kicad_sch", rules=[polarity("C1", plus="VIN", minus="GND")])
    result = guard(tmp_path / "b.kicad_sch", ["true"], read=read, spec=spec)

    assert result.check is not None
    assert result.check.verdict == "fail"
    assert not result.ok


def test_an_ordinary_edit_is_not_a_failure(tmp_path: Path) -> None:
    """Changing the design is the point; only a violation or a defect signature fails."""
    before = _rail("VIN", "GND")
    after = build_netlist(
        [
            Net("VIN", frozenset({Node("R1", "1"), Node("C1", "1"), Node("C7", "1")})),
            Net("GND", frozenset({Node("R1", "2"), Node("C1", "2"), Node("C7", "2")})),
        ],
        [Component("C1"), Component("R1"), Component("C7")],
    )
    result = guard(tmp_path / "b.kicad_sch", ["true"], read=_FakeDesign(before, after))
    assert not result.diff.empty, "a decoupling cap was added"
    assert result.ok, "adding a part is an edit, not a violation"


def test_a_command_that_cannot_start_is_reported_not_raised() -> None:
    result = run_command(["definitely-not-a-real-binary-xyz"])
    assert result.failed_to_start
    assert not result.ok


def test_a_failing_command_makes_guard_fail(tmp_path: Path) -> None:
    same = _rail("VIN", "GND")
    result = guard(tmp_path / "b.kicad_sch", ["false"], read=_FakeDesign(same, same))
    assert not result.command.ok
    assert not result.ok
