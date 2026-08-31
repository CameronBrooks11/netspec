# AGENTS.md — netspec

Verify PCB connectivity against declared engineering intent, using KiCad as the oracle.

## The rules that are not negotiable

Read `docs/DECISIONS.md` before changing anything structural. Four decisions are enforced
by tests, and a change that trips one is a design change, not a bug fix:

1. **netspec never writes to a design file** (D2). Not schematics, boards, or libraries.
   If a feature needs to mutate a design, it belongs in a different project.
2. **Never `import pcbnew`** (D3). It is already deleted in KiCad master.
3. **KiCad only leaks in through `oracle/`** (D4). No `kicad-cli` string and no
   `subprocess` import anywhere else — that boundary is the whole KiCad 11 migration plan.
4. **No tolerances** (D9). Connectivity is exact. The words *tolerance*, *approximate* and
   *interval* are banned from the model; they are partspec's epistemics, not ours.

## KiCad facts

Never plan against KiCad's roadmap wiki — it is stale (D6). Verify against `master` by
counting `registerHandler<>` calls, and pin what you rely on in
`tests/test_kicad_assumptions.py`.

`just assumptions <path-to-kicad-checkout>` re-verifies on demand; CI does it weekly.

## Workflow

`just check && just test` before every commit. Conventional Commits. Never `--no-verify`.

## Related

Sibling projects, deliberately non-overlapping: `partspec` (mechanical CAD parts),
`gerberdiff` (fabrication output). Design review belongs to `kicad-happy`; authoring
belongs to Konnect / SKiDL / atopile.
