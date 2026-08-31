# netspec

**Verify PCB connectivity against declared engineering intent, using KiCad as the oracle.**

> **Status: pre-alpha, unreleased.** `doctor`, `netlist`, `snap` and `diff` work;
> `check` and `guard` do not exist yet. The design lives in
> [`docs/DECISIONS.md`](docs/DECISIONS.md).

Every tool that edits a KiCad design reports on its own arithmetic. Only KiCad knows what
a design actually *is*. `netspec` asks it — after every change — and fails loudly when
intent and reality diverge.

```sh
netspec doctor                                     # find and probe a KiCad engine
netspec netlist board.kicad_sch                    # what KiCad says is connected
netspec snap board.kicad_sch -o before.json        # record connectivity, stably
netspec diff before.json board.kicad_sch           # what actually changed

# not built yet
netspec check board.kicad_sch                      # assert a contract
netspec guard board.kicad_sch -- <any tool>        # snapshot, run, re-read, adjudicate
```

```
$ netspec netlist tests/fixtures/good_ldo.kicad_sch
  3 components, 3 nets
    +3V3  C2.1, U1.2
    GND   C1.2, C2.2, U1.1
    VIN   C1.1, U1.3
```

Finds `kicad-cli` on `PATH`, via Flatpak, in a macOS bundle or a Windows install --
`netspec doctor` says which. A missing engine is an environment fault (exit `4`), never a
finding about your design.

`netspec` **never writes to a design file.** It is the gate an editing tool's output has
to pass, not the tool.

## Why

A survey of ~90 KiCad AI/agent projects found three failure modes that ship today, and one
property of KiCad's own defaults, all of which a netlist check catches and nothing else
does:

- wires "added" that connect nothing, reported as success
- a pin-position helper off by a sign, silently wiring pin 2 where pin 1 was asked for —
  which reverses a polarised capacitor while passing ERC
- `pcb drc --schematic-parity` at default severity reporting **0 problems on a board with
  147**, because `footprint_symbol_mismatch` defaults to `warning`

Each is frozen as a regression test in [`tests/fixtures/`](tests/fixtures), paired with
the netlist KiCad itself derived from it.

Two of those fixtures are the same circuit, wired correctly and wired backwards. KiCad's
ERC reports **the same two violations for both** -- reversing a polarised capacitor is
legal wiring, so no rule fires. `netspec` names it:

```
$ netspec diff polarized_cap_correct.kicad_sch reversed_polarized_cap.kicad_sch
NET CHANGES
  ~ Net-(C1-Pad2)  +C1.2 -C1.1

FLOATING PINS
  ! C1.1  is no longer connected to anything

PIN SWAPS  (a connection moved between pins of one part)
  C1 on Net-(C1-Pad2): pin 1 -> pin 2  <- reverses a 2-pin part

WARNING: 1 pin swap(s) on a two-pin part. If any is polarised, it is now backwards,
and ERC will not tell you.
```

## Family

- **[partspec](https://github.com/CameronBrooks11/partspec)** — mechanical CAD parts vs declared intent
- **netspec** — PCB nets vs declared intent
- **[gerberdiff](https://github.com/CameronBrooks11/gerberdiff)** — fabrication output geometry

## Not this

`netspec` does not do design review (see [kicad-happy](https://github.com/aklofas/kicad-happy)),
does not edit schematics or boards (see [Konnect](https://github.com/mixelpixx/Konnect)),
and does not place or route. It holds the ruler.

## License

Apache-2.0
