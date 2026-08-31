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

## D14 — Name: `netspec`

CLI and import-facing name is `netspec`, in the family idiom: `partspec` for mechanical
CAD parts, `netspec` for PCB nets, `gerberdiff` for fabrication output.

The bare `netspec` name on PyPI is held by an abandoned package — last release
2023-09-04, GitHub repo deleted, summary still unedited cookiecutter boilerplate, ~9
downloads a month. Rather than block on a PEP 541 transfer, the distribution is
**`kicad-netspec`** and the import package is `kicad_netspec`, while the console script
stays `netspec`. A PEP 541 request for the bare name can run in the background; if it
succeeds, publish an alias distribution.
