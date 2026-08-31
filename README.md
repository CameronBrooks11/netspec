# netspec

**Verify PCB connectivity against declared engineering intent, using KiCad as the oracle.**

> **Status: pre-alpha, unreleased.** Nothing works yet. The design lives in
> [`docs/DECISIONS.md`](docs/DECISIONS.md); the reason it exists lives in
> [`docs/FAILURE-MODES.md`](docs/FAILURE-MODES.md).

Every tool that edits a KiCad design reports on its own arithmetic. Only KiCad knows what
a design actually *is*. `netspec` asks it — after every change — and fails loudly when
intent and reality diverge.

```sh
netspec check board.kicad_sch                      # assert a contract
netspec diff before.json board.kicad_sch           # what actually changed
netspec guard board.kicad_sch -- <any tool>        # snapshot, run, re-read, adjudicate
```

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

See [`docs/FAILURE-MODES.md`](docs/FAILURE-MODES.md) for the reproductions.

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
