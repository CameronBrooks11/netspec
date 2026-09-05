"""Adjudicate a contract against what KiCad says.

Four statuses (DECISIONS D9), of which only ``pass`` is green::

    pass         evaluated and satisfied
    fail         evaluated and violated
    unsupported  this backend cannot evaluate this rule
    skipped      not evaluated -- a part or net the rule names is not in the design

``skipped`` is deliberately not green. A rule about a component that is missing has not
been satisfied; it has not been tested, and silently treating that as success is how a
contract stops protecting anything.

There is no fifth status for an indeterminate result, because connectivity is exact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from kicad_netspec.contract import Forbid, Net, Polarity, Rule, Spec
from kicad_netspec.model import Netlist
from kicad_netspec.resolve import Resolved, resolve_spec

__all__ = ["CheckReport", "CheckResult", "check_spec"]

Status = Literal["pass", "fail", "unsupported", "skipped"]
Verdict = Literal["pass", "fail"]


@dataclass(frozen=True)
class CheckResult:
    """One rule, adjudicated."""

    rule: str
    status: Status
    detail: str = ""

    @property
    def green(self) -> bool:
        return self.status == "pass"

    def __str__(self) -> str:
        mark = {"pass": "ok  ", "fail": "FAIL", "unsupported": "n/a ", "skipped": "skip"}[
            self.status
        ]
        return f"{mark}  {self.rule}" + (f"\n        {self.detail}" if self.detail else "")


@dataclass(frozen=True)
class CheckReport:
    """Every rule in a contract, adjudicated against one reading of the design."""

    results: tuple[CheckResult, ...] = ()
    source: str = ""
    kicad_version: str = ""

    @property
    def verdict(self) -> Verdict:
        """Green only when there was something to check and every rule passed.

        A report with no results is not green: ``all([])`` is ``True``, so an empty
        contract would otherwise exit 0 while protecting nothing.
        """
        return "pass" if self.results and all(r.green for r in self.results) else "fail"

    def of_status(self, status: Status) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status == status)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return self.of_status("fail")

    def __str__(self) -> str:
        counts = {s: len(self.of_status(s)) for s in ("pass", "fail", "unsupported", "skipped")}
        return (
            f"CheckReport({self.verdict}: " + ", ".join(f"{v} {k}" for k, v in counts.items()) + ")"
        )


def check_spec(spec: Spec, netlist: Netlist) -> CheckReport:
    """Adjudicate every rule in ``spec`` against ``netlist``."""
    resolved = resolve_spec(spec, netlist)
    results = [_adjudicate(rule, netlist, resolved) for rule in spec.rules]
    if spec.require_no_floating_pins:
        results.append(_check_no_floating(netlist))
    if not results:
        results.append(
            CheckResult(
                rule="this contract asserts something",
                status="fail",
                detail="the contract has no rules, so it checked nothing and protects nothing",
            )
        )
    return CheckReport(
        results=tuple(results),
        source=netlist.source,
        kicad_version=netlist.kicad_version,
    )


def _adjudicate(rule: Rule, netlist: Netlist, resolved: Resolved) -> CheckResult:
    """Evaluate one rule, unless its names do not pick out one net each.

    An ambiguous name is a defect in the *contract*, not a finding about the board, so
    it is reported before the rule runs rather than resolved by guesswork. Absence is
    left to the rule: for ``forbid`` a vanished net is the answer, not an obstacle.
    """
    problems = resolved.problems_for(rule)
    if problems:
        return CheckResult(rule=str(rule), status="fail", detail="; ".join(problems))
    return _check_rule(rule, netlist, resolved)


def _check_rule(rule: Rule, netlist: Netlist, resolved: Resolved) -> CheckResult:
    if isinstance(rule, Net):
        return _check_net(rule, netlist, resolved)
    if isinstance(rule, Polarity):
        return _check_polarity(rule, netlist, resolved)
    if isinstance(rule, Forbid):
        return _check_forbid(rule, netlist, resolved)
    return CheckResult(rule=str(rule), status="unsupported", detail="unknown rule type")


def _check_net(rule: Net, netlist: Netlist, resolved: Resolved) -> CheckResult:
    label = str(rule)

    found = netlist.nets.get(resolved.net(rule.name) or rule.name)
    if found is None:
        return CheckResult(
            rule=label,
            status="fail",
            detail=f"there is no net called {rule.name!r} in this design",
        )

    wanted: set[str] = set()
    for pin in rule.pins:
        node = netlist.resolve(pin)
        if node is None:
            return CheckResult(
                rule=label,
                status="skipped",
                detail=f"{pin} is not a pin in this design, so the rule was not evaluated",
            )
        wanted.add(str(node))

    actual = {str(n) for n in found.nodes}
    missing = sorted(wanted - actual)
    extra = sorted(actual - wanted)

    if missing or (rule.exact and extra):
        parts = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if extra and rule.exact:
            parts.append(f"unexpected {', '.join(extra)}")
        return CheckResult(rule=label, status="fail", detail="; ".join(parts))
    return CheckResult(rule=label, status="pass")


def _check_polarity(rule: Polarity, netlist: Netlist, resolved: Resolved) -> CheckResult:
    label = (
        f"{rule.ref} polarity: pin {rule.plus_pin}->{rule.plus}, pin {rule.minus_pin}->{rule.minus}"
    )

    if rule.ref not in netlist.components:
        return CheckResult(rule=label, status="skipped", detail=f"{rule.ref} is not in this design")

    actual_plus = netlist.net_of(rule.ref, rule.plus_pin)
    actual_minus = netlist.net_of(rule.ref, rule.minus_pin)

    # Compare against what the design calls these nets, so a contract may name a
    # hierarchical net by its leaf the same way it does everywhere else.
    want_plus = resolved.net(rule.plus) or rule.plus
    want_minus = resolved.net(rule.minus) or rule.minus

    wrong = []
    if actual_plus != want_plus:
        wrong.append(f"pin {rule.plus_pin} is on {actual_plus or 'nothing'}, expected {rule.plus}")
    if actual_minus != want_minus:
        wrong.append(
            f"pin {rule.minus_pin} is on {actual_minus or 'nothing'}, expected {rule.minus}"
        )

    if not wrong:
        return CheckResult(rule=label, status="pass")

    # The specific, dangerous case: the two are simply the wrong way round.
    reversed_ = actual_plus == want_minus and actual_minus == want_plus
    detail = "; ".join(wrong)
    if reversed_:
        detail += f"  -- {rule.ref} IS REVERSED. ERC does not check this."
    return CheckResult(rule=label, status="fail", detail=detail)


def _check_forbid(rule: Forbid, netlist: Netlist, resolved: Resolved) -> CheckResult:
    label = str(rule)

    targets = {n: resolved.net(n) for n in rule.nets}
    present = [n for n, t in targets.items() if t]
    absent = [n for n, t in targets.items() if not t]

    # This is what a short actually looks like. KiCad does not emit a pin sitting on two
    # named nets -- it MERGES the nets and keeps one name, so the other simply
    # disappears. A net a contract declared distinct that has vanished is therefore the
    # signature of a short, which LVS calls an n:1 merge. Verified against kicad-cli:
    # relabelling VIN to GND in a three-net design yields two nets, and no VIN.
    if absent:
        if not present:
            return CheckResult(
                rule=label,
                status="fail",
                detail=(
                    f"none of these nets exist in this design: {', '.join(absent)} -- "
                    "the rule names nothing real, so nothing was checked"
                ),
            )
        return CheckResult(
            rule=label,
            status="fail",
            detail=(
                f"gone: {', '.join(absent)}; still here: {', '.join(present)}. "
                "A short merges two nets and keeps one of the names, so a net this "
                "contract declared separate that has vanished is what a short looks like."
            ),
        )

    return CheckResult(rule=label, status="pass")


def _check_no_floating(netlist: Netlist) -> CheckResult:
    floating = netlist.isolated_nodes
    label = "no pin is left unconnected"
    if not floating:
        return CheckResult(rule=label, status="pass")
    shown = ", ".join(str(n) for n in floating[:10])
    more = f" (and {len(floating) - 10} more)" if len(floating) > 10 else ""
    return CheckResult(rule=label, status="fail", detail=f"{len(floating)} floating: {shown}{more}")
