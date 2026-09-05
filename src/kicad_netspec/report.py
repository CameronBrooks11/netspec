"""The machine-readable report D2 promises.

D2 states netspec's product as "a persisted, schema'd report and an exit code that gates
CI". Until this module there was only the exit code: a result's ``rule`` was a rendered
English sentence and no command emitted JSON. Three primitives of prose an agent can
read; the vocabulary being built here has nine, and it cannot.

**Every result carries an ``id``, and that is the load-bearing part.** D8 answers the
objection "an agent can silently weaken an assertion" with "a semantic report diff that
reports removed assertions" -- which only works if two reports can be *aligned*. The id
is ``kind:subject``: stable across runs, stable when the assertion is edited, different
when it is about something else. So a rule that has been deleted goes missing from the
id set, while a rule that has been quietly weakened keeps its id and changes its body.
Those are different findings and a report that cannot tell them apart answers nothing.

The rendered sentence is not thrown away; it is demoted to one field, ``text``, among
the fields a machine actually wants.
"""

from __future__ import annotations

import hashlib
from typing import Any

from kicad_netspec import __version__
from kicad_netspec.check import CheckReport, CheckResult

__all__ = ["REPORT_SCHEMA", "check_report"]

REPORT_SCHEMA = 1
"""Bumped when the document's shape changes in a way a reader could trip over."""

_STATUSES = ("pass", "fail", "unsupported", "skipped")


def check_report(report: CheckReport) -> dict[str, Any]:
    """Render an adjudicated contract as a JSON-safe document."""
    return {
        "schema": REPORT_SCHEMA,
        "netspec": __version__,
        "kind": "check",
        "source": report.source,
        "kicad_version": report.kicad_version,
        "verdict": report.verdict,
        "counts": {s: len(report.of_status(s)) for s in _STATUSES},
        "results": [_result(r) for r in report.results],
    }


def _result(result: CheckResult) -> dict[str, Any]:
    data = dict(result.data)
    kind = str(data.pop("kind", "") or "none")
    subject = str(data.pop("subject", ""))

    return {
        "id": _identify(kind, subject, result.rule),
        "kind": kind,
        "subject": subject,
        "status": result.status,
        "detail": result.detail,
        "text": result.rule,
        **data,
    }


def _identify(kind: str, subject: str, text: str) -> str:
    """``kind:subject`` where there is one, else a digest of the rendered sentence.

    A synthetic result -- "this contract asserts nothing", or a rule type with no checker
    -- corresponds to no rule and so has no subject. It still needs an id that does not
    collide with the next one, and hashing the only thing it has is honest about that.
    """
    if subject:
        return f"{kind}:{subject}"
    return f"{kind}:{hashlib.sha256(text.encode()).hexdigest()[:12]}"
