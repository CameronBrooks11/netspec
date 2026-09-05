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

__all__ = ["CheckReport", "CheckResult", "check_spec", "net_of_pin"]

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
    results = [_check_rule(rule, netlist) for rule in spec.rules]
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


def net_of_pin(netlist: Netlist, ref: str, pin: str) -> str | None:
    """Net a pin sits on, given either its number or KiCad's pin function name.

    ``Netlist.net_of`` is indexed by pin *number* only. Device:D calls its pins ``A``
    and ``K``, so a contract written the way D12 says to write one -- preferring the
    function name -- resolved to nothing and reported a correct board as broken.
    """
    node = netlist.resolve(f"{ref}.{pin}")
    return netlist.net_of(node.ref, node.pin) if node else None


def _check_rule(rule: Rule, netlist: Netlist) -> CheckResult:
    if isinstance(rule, Net):
        return _check_net(rule, netlist)
    if isinstance(rule, Polarity):
        return _check_polarity(rule, netlist)
    if isinstance(rule, Forbid):
        return _check_forbid(rule, netlist)
    return CheckResult(rule=str(rule), status="unsupported", detail="unknown rule type")


def _check_net(rule: Net, netlist: Netlist) -> CheckResult:
    label = str(rule)

    found = netlist.nets.get(rule.name)
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


def _check_polarity(rule: Polarity, netlist: Netlist) -> CheckResult:
    label = (
        f"{rule.ref} polarity: pin {rule.plus_pin}->{rule.plus}, pin {rule.minus_pin}->{rule.minus}"
    )

    if rule.ref not in netlist.components:
        return CheckResult(rule=label, status="skipped", detail=f"{rule.ref} is not in this design")

    actual_plus = net_of_pin(netlist, rule.ref, rule.plus_pin)
    actual_minus = net_of_pin(netlist, rule.ref, rule.minus_pin)

    wrong = []
    if actual_plus != rule.plus:
        wrong.append(f"pin {rule.plus_pin} is on {actual_plus or 'nothing'}, expected {rule.plus}")
    if actual_minus != rule.minus:
        wrong.append(
            f"pin {rule.minus_pin} is on {actual_minus or 'nothing'}, expected {rule.minus}"
        )

    if not wrong:
        return CheckResult(rule=label, status="pass")

    # The specific, dangerous case: the two are simply the wrong way round.
    reversed_ = actual_plus == rule.minus and actual_minus == rule.plus
    detail = "; ".join(wrong)
    if reversed_:
        detail += f"  -- {rule.ref} IS REVERSED. ERC does not check this."
    return CheckResult(rule=label, status="fail", detail=detail)


def _check_forbid(rule: Forbid, netlist: Netlist) -> CheckResult:
    label = str(rule)

    present = [n for n in rule.nets if n in netlist.nets]
    absent = [n for n in rule.nets if n not in netlist.nets]

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

    # Belt and braces for an oracle that does emit a pin on two nets. KiCad cannot, but
    # nothing in the model forbids it and the cost of looking is nil.
    seen: dict[str, str] = {}
    shared: list[str] = []
    for name in present:
        for node in netlist.nets[name].nodes:
            key = str(node)
            if key in seen and seen[key] != name:
                shared.append(f"{key} is on both {seen[key]} and {name}")
            seen[key] = name

    if shared:
        return CheckResult(rule=label, status="fail", detail="; ".join(sorted(shared)))
    return CheckResult(rule=label, status="pass")


def _check_no_floating(netlist: Netlist) -> CheckResult:
    floating = netlist.isolated_nodes
    label = "no pin is left unconnected"
    if not floating:
        return CheckResult(rule=label, status="pass")
    shown = ", ".join(str(n) for n in floating[:10])
    more = f" (and {len(floating) - 10} more)" if len(floating) > 10 else ""
    return CheckResult(rule=label, status="fail", detail=f"{len(floating)} floating: {shown}{more}")
