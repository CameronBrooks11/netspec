# Decisions

Numbered, dated, and load-bearing. Each records what was decided and *why*, so it can be
revisited deliberately rather than re-litigated by accident. Follows the convention in
[`partspec`](https://github.com/CameronBrooks11/partspec).

---

## D1 — Scope: personal OSS, dogfooded

Public from the start, Apache-2.0, to the same house scaffold and standards as `partspec`,
`gerberdiff` and `scadman`. Dogfooded on real boards before it is promoted anywhere.

## D2 — Product boundary: the stateless gate, not the authoring loop

**netspec never writes to a design file.** Not schematics, not boards, not libraries.

Its product is a persisted, schema'd report and an exit code that gates CI. It holds no
session state and owns no edit loop. Adjacent projects own authoring — Konnect drives a
live KiCad, SKiDL and atopile generate from code, a human uses the GUI. netspec is the
gate their output must pass.

This is `partspec`'s D18, adopted verbatim in substance, and it is what keeps the project
small. Every silent-corruption failure in the survey behind this project lives in a
*writer*; declining to be one removes most of the risk surface and most of the work.

Consequence: an MCP server is in scope (D7) because the boundary is about state and loop
ownership, not transport. Its tools are stateless verbs — every call re-reads from disk.

## D3 — Never depend on SWIG `pcbnew`

`pcbnew/python/swig` exists on the KiCad 10.0 branch and is **already deleted** in master
— zero `.i` files, no `KICAD_SCRIPTING` cmake option. This is an absence, not a
deprecation warning.

Enforced twice: a ruff `banned-api` rule on the `pcbnew` import, and
`tests/test_boundaries.py::test_never_imports_pcbnew`.

Cost accepted: this rules out `kinet2pcb`, and therefore SKiDL's `generate_pcb()`, as
anything netspec depends on. Fine — netspec does not generate boards.

## D4 — KiCad only leaks in through the Oracle protocol

One `Protocol` with two implementations: `Cli10Backend` (subprocess `kicad-cli`, ships
first) and a later `Ipc11Backend`. No `kicad-cli` string and no `subprocess` import
outside `oracle/`, enforced by
`tests/test_boundaries.py::test_kicad_only_leaks_in_through_the_oracle`.

The protocol is deliberately shaped like KiCad 11's own commands
(`GetSchematicNetlist`, `RunSchematicJobExport*`, `ImportNetlist`) so the second backend
is a mapping, not a redesign.

## D5 — Ship on KiCad 10; treat KiCad 11 as a backend, never a prerequisite

Every capability in v0 is a `kicad-cli` subcommand that exists and has been measured:
`sch export netlist --format kicadxml`, `sch erc --format json`,
`pcb drc --format json --schematic-parity`. Netlist export measures 0.44–0.61 s, which is
what makes per-mutation verification affordable.

Nothing is deferred to a release that has not happened. KiCad 11's milestone is
Feb 2027–Feb 2028; that is treated as unknown timing, not a schedule.

## D6 — Plan only against KiCad facts verified in master, and re-verify in CI

KiCad's public roadmap wiki is **stale** — it still describes "a Python wrapper to hide
the SWIG generated API", superseded years ago. It must not be used for planning.

Every KiCad 11 fact this project leans on was read out of `master` by counting
`registerHandler<>` calls, and is pinned in `tests/test_kicad_assumptions.py`. A weekly CI
job re-runs it against KiCad master, so an upstream revert arrives as a red build rather
than a discovery in 2027. Verified to fail correctly against the 10.0 branch.

## D7 — CLI first, MCP later, as an optional extra

The CLI and its report are the product. The MCP server is an `mcp` extra whose tools are
stateless verbs over the same code path. `mcp` is pinned `>=1.27,<2`: nine of twenty-two
servers in the survey are dead on a fresh install because they pinned `mcp>=1.x` with no
upper bound and `mcp` 2.x renamed `FastMCP`.

Target: under 3K tokens of `tools/list` schema, measured in CI, failing the build on
regression.

## D8 — Contract in Python, not sidecar YAML

Carried from `partspec` D6. More expressive, no schema to design or version, and it
avoids the policy-DSL trap. The "an agent can silently weaken an assertion" objection is
answered the same way it is in partspec — a semantic report diff that reports `removed:`
assertions — which a YAML schema would not do better.

Inherits partspec's "a contract is code" consequence: `netspec check` imports and executes
the module it is given. `diff` executes nothing.

## D9 — Four statuses, and no tolerance anywhere

`pass · fail · unsupported · skipped`. Only `pass` is green.

partspec has a fifth, `approximate`, for when a measured interval straddles a threshold.
**It is deliberately absent here.** Connectivity is discrete: two pins are connected or
they are not. There is no error interval, so the status would be unreachable, and
importing partspec's interval epistemics into an exact domain would add concepts without
adding truth.

Guarded by `tests/test_boundaries.py::test_report_carries_no_tolerance`. If the words
*tolerance*, *approximate* or *interval* ever appear in the model, something has gone
wrong.

`unsupported` survives, for a check that needs a capability the active backend lacks.

## D10 — An environment fault is never a failing board

Adopted from partspec, and the most valuable thing taken from it.

No `kicad-cli` on `PATH`, a KiCad that will not start, a source file that is not there —
these are `verdict: "error"`, a distinct exit code, every check `skipped`. They are **not**
statements about the design. A CI run on a machine without KiCad must never report a board
as disproven.

Carried in the report as a field a consumer can branch on, not as prose in a message.

## D11 — Nets are compared structurally, not by name

KiCad auto-names unlabelled nets after their own contents (`Net-(C1-Pad1)`), so changing
connectivity also changes the name. A name-keyed diff would report noise and miss the real
edit.

Nets are classified **named** (a label a human wrote) or **anonymous** (auto-generated).
Named nets diff by name; anonymous nets diff by node-set identity. A pure rename with no
structural change is reported as benign; a structural change under a stable name is
reported loudly.

### D11.1 — A one-node net is a floating pin, not a net (amendment, Phase 2)

KiCad reports every unconnected pin as a net of one node. Diffing those as nets turns a
single edit into a removal plus an addition: moving one connection between two pins of a
part produced *three* reported changes, which is precisely the noise D11 exists to
prevent.

Single-node nets are therefore excluded from net matching and reported as a separate
floating-pin delta -- ``now_floating`` and ``no_longer_floating``. One edit reads as one
change, and "7 pins came free" is more legible than seven phantom net additions.

## D12 — Intent is expressed against pin *functions* where possible

KiCad's `kicadxml` netlist carries `pinfunction` and `pintype` per node. A contract says
`U1.VI`, not `U1.3`.

This matters because pin *numbers* are exactly what the surveyed bugs corrupt: a
sign-flipped pin-position helper wires pin 2 where pin 1 was asked for, silently reversing
a polarised capacitor. An assertion written against the function survives that, and
survives a library renumbering.

## D13 — Rule checks run at `--severity-all` by default

`kicad-cli pcb drc --schematic-parity` at default severity reports **0 violations and 0
parity issues on a board carrying 147**, because KiCad defaults
`footprint_symbol_mismatch` to `warning` (`board_design_settings.cpp`) and tooling that
fails only on errors goes green.

This was found in a real repo where it hid a schematic/PCB mismatch for months with green
CI, and reproduced here on KiCad's own demo board — so it is a property of KiCad's
defaults, not of any one project.

Therefore: netspec passes `--severity-all`, reports severity per finding, and lets the
caller decide what fails — explicitly and in the report. It never infers "clean" from an
exit code; it parses the JSON and counts. A `doctor` check flags any `.kicad_pro` relying
on KiCad's defaults for the parity rules.

### D4.1 — The Oracle boundary bans *KiCad*, not `subprocess` (amendment, Phase 3)

``tests/test_boundaries.py`` originally banned the string ``subprocess`` anywhere outside
``oracle/``. That is the right rule for the wrong reason: what must not leak is *KiCad*,
so that the IPC backend stays one file.

``guard`` has to run an arbitrary command the user names -- an agent, a build script, a
generator -- and that is not KiCad leaking in. The rule is narrowed to name explicit
exemptions, which may spawn a process but must never invoke a KiCad binary. Widening the
list is a design change and is argued for here:

* ``ops/run.py`` — runs the command ``guard`` was pointed at.
* ``mcp.py`` — runs the netspec CLI, one process per call (D18). It does not reach the
  oracle directly and names no KiCad binary; KiCad is still reached only through
  ``oracle/``, one process further down. The test that caught this addition is the
  reason the exemption is a list rather than a blanket allowance.

## D15 — A pin swap's significance comes from the part, not the topology

**Measured, and it overturned the first design.**

A reversed polarised capacitor and a deliberately re-pinned connector produce an
*identical* diff: two pins of one component trading nets. The first implementation
flagged any 1↔2 move as a probable defect. A read-only dry run over a real board's
history — 22 net changes across a genuine re-pinning commit — produced **14 pin swaps,
none of them defects**. A tool that warns on all fourteen teaches its reader to ignore
the warning.

The obvious refinement, "an unpaired swap is a defect, a paired one is an exchange",
was tested and **rejected**: the reversed capacitor pairs up exactly the same way. There
is no topological difference to find.

What separates them is the component. `PinSwap.significance`:

| | meaning |
|---|---|
| `polarity` | terminals are not interchangeable (`C_Polarized`, `CP`, `D*`, `LED`, `Battery`, or pins named `A`/`K`/`+`/`-`). The swap reverses the part, and ERC has no rule against it. |
| `none` | a symmetric two-terminal passive (`R`, `C`, `L`). Swapping its pins changes nothing; reporting it is noise. |
| `repin` | anything else — a connector, an IC. A real change, usually deliberate. Reported without a warning. |

`NetlistDiff.suspicious` returns only the `polarity` swaps, and only those fail a
`guard` run or raise a warning in a pull-request comment.

Two bugs of my own were found while validating this, both now regression-tested:
`str.rstrip("_0123456789")` reduced an Arduino's pin `A6_25` to `"a"` and called it an
anode; and a bare `"d"` symbol prefix matched `Device`, `Driver` and `DIP`. Both
heuristics are now anchored.

**The lesson worth keeping:** this class of judgement cannot be designed from first
principles, only measured against designs you did not write.

## D16 — The Action bootstraps pip, because KiCad CI images do not ship it

`ghcr.io/inti-cmnb/kicad10_auto` — the natural place to run this, and the family KiBot
users already run — ships Python 3.13 with **no pip, no pip3, no pipx and no uv**, and
no `curl` or `wget` to fetch one. `ensurepip` is absent too, so `python3 -m venv` cannot
bootstrap. Probed directly against the image rather than inferred.

The image is Debian, so the action installs `python3-pip` from apt (~20 s) when pip is
missing. The distro Python is additionally marked externally managed (PEP 668), so a
plain install refuses; adding one dependency-free package to a throwaway CI container is
safe, and the install falls back to `--break-system-packages` rather than failing.

The action also marks the workspace a safe directory: reading the base revision uses a
git worktree, and a checkout made by a different uid inside a container trips git's
ownership check.

This is the kind of thing that is only discoverable by running it. Verified end to end
inside the real image, which is also the first confirmation that netspec works against a
**native** `kicad-cli` rather than a Flatpak-wrapped one.

## D17 — Releases publish from a tag on main, and what that does not yet prove

`release.yml` runs **no tests**. It verifies the artifact — builds, metadata is valid,
the wheel installs cold into an empty venv, `--version` agrees with the tag, and nothing
but `kicad-netspec` lands in that venv (the zero-dependency claim, met against the built
wheel rather than the source tree). Correctness is the `Check` gate's job on main.

Two gates make that argument hold rather than merely stating it:

* `scripts/assert_tag_on_main.sh` — refuses a tag that is not an ancestor of
  `origin/main`, and refuses when it cannot resolve `origin/main` at all rather than
  assuming. A script, not inline YAML, so the test suite can exercise it against
  throwaway repositories; an inline gate can only be grepped, and dropping one `!` would
  leave it green and inert.
* The tag must equal `project.version`, or the release would publish a version whose
  name lies about its content.

**The gap, now closed.** Being on main proves the commit is on main; it proves the gate
*passed* only if main requires `Check`. It now does:

| setting | value |
|---|---|
| required status checks | `ok` (the bare context, never individual job names) |
| require branches up to date | yes |
| require a pull request | yes, 0 approvals |
| do not allow bypassing | **yes** |
| linear history | yes |
| force pushes / deletions | no |
| conversation resolution | required |

Taken from the house security baseline. The `ok` gate is a single aggregating job every
other job feeds into, so adding or renaming CI jobs never means touching branch
protection.

One divergence from `partspec`, deliberately: it runs with admin bypass *enabled*, and
netspec does not. Verified rather than assumed — with bypass on, a direct push to main
returned `Bypassed rule violations` and **landed anyway**; with it off the same push is
`remote rejected`. Since this workflow's entire safety argument is "every commit on main
passed `Check`", a bypass an admin can take at 3am is the argument's weakest link, and
this is the repo where that argument gates a PyPI upload.

The cost is real: every change here now needs a pull request, including a one-line
docs fix, and there is no emergency escape hatch short of turning the setting off.

Publishing is PyPI Trusted Publishing (OIDC); no token exists in this repo. The
registration names `CameronBrooks11/netspec`, workflow `release.yml`, environment
`pypi`, project `kicad-netspec`. Renaming any of them breaks releases *silently* — the
workflow runs and the upload is rejected — so a test asserts the filename and the
environment.

Version note: tags `v0.2.0`–`v0.4.1` were cut while `pyproject` still said `0.0.1`, so
they claimed versions the package never declared. `0.5.0` is the first version where the
two agree, and the gate above now prevents a recurrence.

## D18 — The MCP server is stateless verbs, and does not expose `guard`

Six tools — ``doctor``, ``netlist``, ``snapshot``, ``diff``, ``check``, ``gate`` — each
a fresh read of a design on disk. Nothing is held between calls.

**A process per call, not an in-process call.** Two reasons, both load-bearing:

* ``check`` imports a contract module and ``sys.modules`` caches it, so a long-lived
  server re-checking an edited contract could adjudicate the *previous* version while
  reporting on the new one. A stale answer presented as fresh is the exact failure this
  project exists to catch; it would be indefensible to ship it in the tool that catches
  it.
* The exit code is part of the contract — ``0`` clean, ``1`` a finding about the design,
  ``4`` an environment fault. A subprocess returns the CLI's own, unlaundered, and each
  reply carries a plain-language ``meaning`` so an agent cannot mistake "I could not
  look" for "your board is broken".

**``guard`` is deliberately absent.** It runs an arbitrary command. An agent driving this
server can already run commands, so exposing it would add risk and no capability; the
agent should make its edit and then call ``diff``. A test asserts it stays absent, along
with the absence of any tool whose name implies writing to a design.

**The tool list has a budget: under 3,000 tokens, asserted in CI.** The survey behind this
project measured KiCad MCP servers from 2,574 to 48,627 tokens of schema — the largest
spending a quarter of a 200K window before the agent reads a file. netspec's six tools
measure ~834 tokens on the wire. Tool surface is a cost paid by every agent that
connects, and a number in CI is the only thing that keeps it from creeping.

## D19 — A contract's net names are resolved before any rule is evaluated

A contract is written by a person; a netlist is named by KiCad. On a hierarchical board
those disagree. KiCad calls a net ``/Channel1/OUT``, and a contract that says ``OUT`` is
abbreviated rather than wrong. Before this, every such rule failed with *"there is no
net called 'OUT' in this design"* — a message that is both untrue and unactionable, and
which made netspec unusable on any multi-sheet design.

Resolution is therefore its own phase, running over the whole contract before the first
rule is adjudicated, with three outcomes:

===========  ===========================================================================
exact        the design has a net by exactly that name; it wins outright
leaf         exactly one net's last path segment matches
ambiguous    more than one matches, and there is no correct guess
===========  ===========================================================================

**The third outcome is why this is a phase and not a lookup helper.** Any design built
from a repeated block has the same net name under several sheets — this was checked
against a real dual-channel board, where a naive leaf name matched both channels for
every net inside the repeated sheet. Choosing one would invent intent the contract never
expressed, so resolution refuses and names the candidates. That is reported as a defect
in the *contract* — the rule fails before it runs — rather than as a finding about the
board. ``tests/fixtures/hierarchy.kicad_sch`` holds the same shape in miniature.

**Ambiguity pre-empts a rule; absence does not.** They are different answers and
collapsing them would undo the fix that taught ``forbid`` to see a real short: a net
that has vanished is precisely how a short appears in a netlist, because KiCad merges
the two nets and keeps one name. ``forbid`` needs to see that absence rather than be
stopped by it. A rule that cannot pick out one
net cannot run at all; a rule whose net is missing usually just failed.

**Only net names are resolved.** Components and pins are named exactly, are unique across
a design, and have no abbreviated form. There is nothing to resolve, so nothing is
resolved, and `Resolution` carries no ``kind`` field it would never use.

**What this does not change.** ``skipped`` still means "a part this rule names is not in
the design", and is still not green. Whether that should instead be a failure — and
whether ``skipped`` survives at all once variant scoping exists — is a separate question
from name resolution and is deliberately left open here.

## D14 — Name: `netspec`

CLI and import-facing name is `netspec`, in the family idiom: `partspec` for mechanical
CAD parts, `netspec` for PCB nets, `gerberdiff` for fabrication output.

The bare `netspec` name on PyPI is held by an abandoned package — last release
2023-09-04, GitHub repo deleted, summary still unedited cookiecutter boilerplate, ~9
downloads a month. Rather than block on a PEP 541 transfer, the distribution is
**`kicad-netspec`** and the import package is `kicad_netspec`, while the console script
stays `netspec`. A PEP 541 request for the bare name can run in the background; if it
succeeds, publish an alias distribution.
