# netspec

**Verify PCB connectivity against declared engineering intent, using KiCad as the oracle.**

> **Status: pre-alpha, unreleased.** All six commands work. The design lives in
> [`docs/DECISIONS.md`](docs/DECISIONS.md).

Every tool that edits a KiCad design reports on its own arithmetic. Only KiCad knows what
a design actually *is*. `netspec` asks it — after every change — and fails loudly when
intent and reality diverge.

```sh
netspec doctor                                     # find and probe a KiCad engine
netspec netlist board.kicad_sch                    # what KiCad says is connected
netspec snap board.kicad_sch -o before.json        # record connectivity, stably
netspec diff before.json board.kicad_sch           # what actually changed
netspec check contract.py                          # adjudicate declared intent
netspec gate board.kicad_pcb                       # ERC/DRC at every severity
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

## Declaring intent

A contract is Python, not a data file -- more expressive, nothing to version, and no
policy DSL to invent. Pins are named by *function* where the symbol offers one, because
pin numbers are exactly what the known schematic-writer bugs corrupt.

```python
from kicad_netspec import Spec, net, polarity, forbid

board = Spec(
    source="hardware/board.kicad_sch",
    rules=[
        net("VIN",  ["J1.1", "C1.1", "U1.VI"]),
        net("+3V3", ["U1.VO", "C2.1"]),
        polarity("C1", plus="VIN", minus="GND"),
        forbid("VIN", "GND"),
    ],
)
```

`netspec check` imports and executes that module -- a contract is code. `netspec diff`
executes nothing.

## Guarding an edit

`guard` does not care what did the editing:

```
$ netspec guard board.kicad_sch --contract contract.py -- claude -p "add a decoupling cap"

NET CHANGES
  ~ GND  +C1.1 -C1.2
  ~ VIN  +C1.2 -C1.1

PIN SWAPS  (a connection moved between pins of one part)
  C1 on VIN: pin 1 -> pin 2  <- reverses a 2-pin part

CONTRACT
  FAIL  C1 polarity: pin 1->VIN, pin 2->GND
        pin 1 is on GND, expected VIN  -- C1 IS REVERSED. ERC does not check this.
```

Exit codes are the contract: `0` clean, `1` a violation of the design, `4` an
environment fault -- a missing engine is never reported as a broken board.

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
