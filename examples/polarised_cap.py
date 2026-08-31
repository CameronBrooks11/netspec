"""A contract that catches a reversed electrolytic capacitor.

C1 is a 100uF polarised capacitor across the rail. Its plus terminal (pin 1) belongs on
VIN; its minus terminal (pin 2) belongs on GND. Wire it the other way and the board is
still electrically legal -- KiCad's ERC has no rule against it and reports the same
verdict either way -- but the part will vent when powered.

    netspec check examples/polarised_cap.py
"""

from kicad_netspec import Spec, net, polarity

board = Spec(
    name="polarised cap across the rail",
    source="../tests/fixtures/polcap_rail_correct.kicad_sch",
    rules=[
        net("VIN", ["J1.1", "C1.1", "R1.1"]),
        net("GND", ["J1.2", "C1.2", "R1.2"]),
        polarity("C1", plus="VIN", minus="GND"),
    ],
)
