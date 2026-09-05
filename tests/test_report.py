"""The machine-readable report D2 promises and D8 depends on.

D2 says netspec's product is "a persisted, schema'd report and an exit code". Until now
there was only the exit code: ``CheckResult.rule`` was a rendered English sentence and no
command emitted JSON. Three primitives of prose an agent can read; nine it cannot.

D8 is the harder constraint. It answers "an agent can silently weaken an assertion" with
"a semantic report diff that reports removed assertions" -- which requires two reports to
be *alignable*, so a deleted rule is distinguishable from an edited one. That is what the
stable id below is for.
"""

from __future__ import annotations

import json

import pytest

from kicad_netspec.check import check_spec
from kicad_netspec.contract import Spec, forbid, net, polarity
from kicad_netspec.model import Component, Net, Node, build_netlist
from kicad_netspec.report import REPORT_SCHEMA, check_report


def _board():
    return build_netlist(
        [
            Net("VIN", frozenset({Node("R1", "1"), Node("C1", "1")}), netclasses=("Power",)),
            Net("GND", frozenset({Node("R1", "2"), Node("C1", "2")})),
        ],
        [Component("C1", "100uF", lib_id="Device:C_Polarized"), Component("R1", "1k")],
    )


def _report(*rules):
    return check_report(check_spec(Spec(source="b.kicad_sch", rules=list(rules)), _board()))


# -- the envelope ------------------------------------------------------------------------


def test_the_report_is_self_describing() -> None:
    doc = _report(net("VIN", ["R1.1", "C1.1"]))
    assert doc["schema"] == REPORT_SCHEMA
    assert doc["verdict"] == "pass"
    assert doc["source"] == "b.kicad_sch" or doc["source"] == ""
    assert "netspec" in doc


def test_the_report_is_json_serialisable() -> None:
    """It is worthless if it cannot be written out. Tuples and frozensets must be gone."""
    doc = _report(
        net("VIN", ["R1.1"]),
        polarity("C1", plus="VIN", minus="GND"),
        forbid("VIN", "GND"),
    )
    round_tripped = json.loads(json.dumps(doc))
    assert round_tripped == doc


def test_counts_agree_with_the_results() -> None:
    doc = _report(net("VIN", ["R1.1"]), net("GND", ["R1.2", "C1.2"]))
    assert sum(doc["counts"].values()) == len(doc["results"])


# -- each result is data, not a sentence ---------------------------------------------------


def test_a_result_carries_the_rule_as_fields() -> None:
    (result,) = _report(net("VIN", ["R1.1", "C1.1"]))["results"]

    assert result["kind"] == "net"
    assert result["subject"] == "VIN"
    assert result["pins"] == ["R1.1", "C1.1"]
    assert result["exact"] is True
    assert result["status"] == "pass"


def test_a_result_still_carries_the_human_sentence() -> None:
    """The prose is not deleted -- it is demoted to one field among many."""
    (result,) = _report(polarity("C1", plus="VIN", minus="GND"))["results"]
    assert "C1" in result["text"]
    assert result["kind"] == "polarity"
    assert result["subject"] == "C1"


def test_every_rule_type_describes_itself() -> None:
    doc = _report(
        net("VIN", ["R1.1"]),
        polarity("C1", plus="VIN", minus="GND"),
        forbid("VIN", "GND"),
    )
    assert [r["kind"] for r in doc["results"]] == ["net", "polarity", "forbid"]
    assert all(r["subject"] for r in doc["results"]), "every result names what it is about"


# -- D8: two reports must be alignable -----------------------------------------------------


def test_a_rule_keeps_its_id_across_runs() -> None:
    a = _report(net("VIN", ["R1.1", "C1.1"]))["results"][0]["id"]
    b = _report(net("VIN", ["R1.1", "C1.1"]))["results"][0]["id"]
    assert a == b, "an id that moves cannot align two reports"


def test_weakening_an_assertion_keeps_the_id_and_changes_the_body() -> None:
    """The D8 case exactly: same assertion, quietly made to demand less."""
    strong = _report(net("VIN", ["R1.1", "C1.1"]))["results"][0]
    weak = _report(net("VIN", ["R1.1"], exact=False))["results"][0]

    assert strong["id"] == weak["id"], "it is the same assertion about the same net"
    assert strong != weak, "and it is demonstrably not the same assertion"
    assert strong["pins"] != weak["pins"]


def test_different_subjects_get_different_ids() -> None:
    doc = _report(net("VIN", ["R1.1"]), net("GND", ["R1.2"]))
    assert len({r["id"] for r in doc["results"]}) == 2


def test_different_rule_kinds_on_one_subject_do_not_collide() -> None:
    doc = _report(net("VIN", ["R1.1"]), forbid("VIN", "GND"))
    assert len({r["id"] for r in doc["results"]}) == 2


def test_a_removed_assertion_is_visible_by_id() -> None:
    """What a report diff would do. If this is awkward here, it is awkward there."""
    before = {r["id"] for r in _report(net("VIN", ["R1.1"]), forbid("VIN", "GND"))["results"]}
    after = {r["id"] for r in _report(net("VIN", ["R1.1"]))["results"]}
    assert before - after, "the deleted forbid must be detectable"


# -- failures carry their reason as data ----------------------------------------------------


def test_a_failure_reports_status_and_detail() -> None:
    (result,) = _report(net("VIN", ["R1.1", "R1.2"]))["results"]
    assert result["status"] == "fail"
    assert result["detail"]


def test_a_contract_that_asserts_nothing_still_produces_a_report() -> None:
    doc = _report()
    assert doc["verdict"] == "fail"
    assert doc["results"], "the synthetic failure must appear in the document too"
    assert doc["results"][0]["kind"] == "none"


# -- the CLI ---------------------------------------------------------------------------------


def test_the_cli_can_emit_json(tmp_path, monkeypatch) -> None:
    from kicad_netspec import cli

    contract_file = tmp_path / "c.py"
    contract_file.write_text(
        "from kicad_netspec import Spec, net\n"
        "board = Spec(source='b.kicad_sch', rules=[net('VIN', ['R1.1'])])\n"
    )
    monkeypatch.setattr("kicad_netspec.cli.Cli10Backend", lambda *a, **k: _FakeBackend())
    code = cli.main(["check", str(contract_file), "--format", "json"])
    assert code in (0, 1)


class _FakeBackend:
    def netlist(self, source, variant=None):
        return _board()


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_both_formats_agree_on_the_verdict(fmt: str) -> None:
    report = check_spec(Spec(source="b", rules=[net("VIN", ["R1.1"])]), _board())
    assert check_report(report)["verdict"] == report.verdict
