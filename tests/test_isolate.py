"""Executing a contract without letting it decide what the board is.

D8 makes a contract *code*, deliberately. The consequence, found by review: a contract
runs before the design is read, so it could replace the oracle and netspec would emit a
genuine passing report about a board the contract invented. No validation of the report
could ever catch that, because netspec really did produce it.

Isolation draws the line where it belongs. The contract runs in a child process and can
only hand back **declared rules**; the parent reads the netlist itself. A hostile contract
is then reduced to lying about its own assertions -- which is exactly what a report diff
catches, and exactly what D8 set out to answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_netspec.contract import Spec, forbid, net, polarity
from kicad_netspec.isolate import ContractError, load_isolated


def _write(tmp_path: Path, body: str, name: str = "c.py") -> Path:
    f = tmp_path / name
    f.write_text(body)
    return f


# -- the vector this exists to close ----------------------------------------------------


def test_a_contract_cannot_replace_the_oracle_in_the_parent(tmp_path: Path) -> None:
    """The finding, directly. In-process this made netspec pass a garbage file."""
    from kicad_netspec.oracle import Cli10Backend

    before = Cli10Backend.netlist
    contract = _write(
        tmp_path,
        "from kicad_netspec.oracle import Cli10Backend\n"
        "from kicad_netspec import Spec, net\n"
        "Cli10Backend.netlist = lambda self, s, variant=None: 'FORGED'\n"
        "board = Spec(source='b.kicad_sch', rules=[net('VIN', ['R1.1'])])\n",
    )
    spec = load_isolated(str(contract))

    assert Cli10Backend.netlist is before, "the parent's oracle was replaced"
    assert spec.source == "b.kicad_sch", "the contract's declarations still arrive"


def test_a_contract_cannot_mutate_the_parents_module_state(tmp_path: Path) -> None:
    """Named for what holds. A contract can still kill the parent, or exhaust it -- both
    denials of service, neither a false pass. It cannot change what the parent believes."""
    import kicad_netspec.report as report_module

    before = report_module.REPORT_SCHEMA
    contract = _write(
        tmp_path,
        "import kicad_netspec.report as r\n"
        "r.REPORT_SCHEMA = 9999\n"
        "from kicad_netspec import Spec, net\n"
        "board = Spec(source='b.kicad_sch', rules=[net('VIN', ['R1.1'])])\n",
    )
    load_isolated(str(contract))
    assert before == report_module.REPORT_SCHEMA


# -- the declarations still have to survive the trip ------------------------------------


def test_an_ordinary_contract_round_trips(tmp_path: Path) -> None:
    contract = _write(
        tmp_path,
        "from kicad_netspec import Spec, net, polarity, forbid\n"
        "board = Spec(\n"
        "    source='b.kicad_sch', name='demo', variant='alt',\n"
        "    require_no_floating_pins=True,\n"
        "    rules=[net('VIN', ['R1.1', 'C1.1']), polarity('C1', plus='VIN', minus='GND'),\n"
        "           forbid('VIN', 'GND')],\n"
        ")\n",
    )
    spec = load_isolated(str(contract))

    expected = Spec(
        source="b.kicad_sch",
        name="demo",
        variant="alt",
        require_no_floating_pins=True,
        rules=[
            net("VIN", ["R1.1", "C1.1"]),
            polarity("C1", plus="VIN", minus="GND"),
            forbid("VIN", "GND"),
        ],
    )
    assert spec == expected


def test_tuple_fields_come_back_as_tuples(tmp_path: Path) -> None:
    """JSON has no tuples. A rule carrying a list is no longer hashable or comparable."""
    contract = _write(
        tmp_path,
        "from kicad_netspec import Spec, net\n"
        "board = Spec(source='b.kicad_sch', rules=[net('VIN', ['R1.1', 'C1.1'])])\n",
    )
    (rule,) = load_isolated(str(contract)).rules
    assert isinstance(rule.pins, tuple)  # type: ignore[attr-defined]
    hash(rule)


def test_naming_one_spec_of_several_still_works(tmp_path: Path) -> None:
    contract = _write(
        tmp_path,
        "from kicad_netspec import Spec, net\n"
        "a = Spec(source='a.kicad_sch', rules=[net('VIN', ['R1.1'])])\n"
        "b = Spec(source='b.kicad_sch', rules=[net('GND', ['R1.2'])])\n",
    )
    assert load_isolated(f"{contract}:b").source == "b.kicad_sch"


# -- how a bad contract is reported -----------------------------------------------------


def test_a_contract_that_raises_is_reported_with_its_error(tmp_path: Path) -> None:
    contract = _write(tmp_path, "raise RuntimeError('boom in the contract')\n")
    with pytest.raises(ContractError, match="boom in the contract"):
        load_isolated(str(contract))


def test_a_contract_that_exits_is_reported_rather_than_believed(tmp_path: Path) -> None:
    contract = _write(tmp_path, "import sys\nsys.exit(0)\n")
    with pytest.raises(ContractError):
        load_isolated(str(contract))


def test_output_on_any_descriptor_does_not_corrupt_the_result(tmp_path: Path) -> None:
    """The result travels through a file, so stdout is not a channel to fight over."""
    contract = _write(
        tmp_path,
        "import os, sys\n"
        "print('chatty')\n"
        'os.write(1, b\'{"not": "a spec"}\')\n'
        "sys.stderr.write('noisy\\n')\n"
        "from kicad_netspec import Spec, net\n"
        "board = Spec(source='b.kicad_sch', rules=[net('VIN', ['R1.1'])])\n",
    )
    assert load_isolated(str(contract)).source == "b.kicad_sch"


def test_an_atexit_handler_gets_no_turn_after_the_result_is_written(tmp_path: Path) -> None:
    """The child ends with os._exit, so nothing registered for interpreter shutdown runs.

    Deliberately not asserted for a *concurrent thread*: that is a genuine race, and it
    is one the contract sometimes wins. It gains nothing by winning -- substituting its
    own declarations is a power a contract already has by writing different rules -- so
    it is the permitted residue D24 describes, not a hole. Asserting otherwise gave a
    test that failed about one run in three.
    """
    contract = _write(
        tmp_path,
        "import atexit, json, sys\n"
        "def _overwrite():\n"
        "    open(sys.argv[-1], 'w').write(json.dumps({'source': 'FORGED', 'rules': []}))\n"
        "atexit.register(_overwrite)\n"
        "from kicad_netspec import Spec, net\n"
        "board = Spec(source='b.kicad_sch', rules=[net('VIN', ['R1.1'])])\n",
    )
    assert load_isolated(str(contract)).source == "b.kicad_sch"


def test_an_unknown_rule_kind_is_a_finding_not_an_environment_fault(tmp_path: Path) -> None:
    """A contract may define its own Rule subclass the parent never imported. That is a
    statement about the contract, so it must not surface as "netspec could not look"."""
    from kicad_netspec.contract import Unknown

    contract = _write(
        tmp_path,
        "from dataclasses import dataclass\n"
        "from typing import ClassVar\n"
        "from kicad_netspec import Spec\n"
        "from kicad_netspec.contract import Rule\n"
        "@dataclass(frozen=True)\n"
        "class Future(Rule):\n"
        "    kind: ClassVar[str] = 'fanout'\n"
        "    name: str\n"
        "    def net_names(self): return ()\n"
        "    def describe(self): return {'subject': self.name}\n"
        "    def __str__(self): return 'future'\n"
        "board = Spec(source='b.kicad_sch', rules=[Future('VIN')])\n",
    )
    (rule,) = load_isolated(str(contract)).rules
    assert isinstance(rule, Unknown)
    assert rule.declared == "fanout"


def test_a_forbid_with_too_few_nets_cannot_be_rebuilt(tmp_path: Path) -> None:
    """The JSON path is a second constructor; Forbid(nets=()) adjudicated as pass."""
    from kicad_netspec.isolate import _restore_rule

    with pytest.raises(ContractError):
        _restore_rule({"kind": "forbid", "fields": {"nets": []}})


def test_the_rebuild_refuses_fields_of_the_wrong_type() -> None:
    from kicad_netspec.isolate import _rebuild

    for bad in ({"source": 5}, {"source": "b", "variant": [1]}, {"source": "b", "rules": "no"}):
        with pytest.raises(ContractError):
            _rebuild(bad)


def test_a_contract_that_never_finishes_is_stopped(tmp_path: Path) -> None:
    contract = _write(tmp_path, "while True:\n    pass\n")
    with pytest.raises(ContractError, match="timed out"):
        load_isolated(str(contract), timeout=2)


def test_a_missing_contract_is_reported_clearly(tmp_path: Path) -> None:
    with pytest.raises(ContractError):
        load_isolated(str(tmp_path / "nope.py"))


def test_a_contract_defining_no_spec_is_reported(tmp_path: Path) -> None:
    contract = _write(tmp_path, "x = 1\n")
    with pytest.raises(ContractError, match="no Spec"):
        load_isolated(str(contract))
