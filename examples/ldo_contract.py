"""A worked contract: the 3.3V LDO in tests/fixtures/good_ldo.kicad_sch.

Run it:

    netspec check examples/ldo_contract.py
"""

from kicad_netspec import Spec, forbid, net

board = Spec(
    name="3V3 LDO",
    source="../tests/fixtures/good_ldo.kicad_sch",
    rules=[
        # Pins are named by function where the symbol offers one, so these assertions
        # survive a library that renumbers pins -- and survive a writer that swaps them.
        net("VIN", ["C1.1", "U1.VI"]),
        net("+3V3", ["C2.1", "U1.VO"]),
        net("GND", ["C1.2", "C2.2", "U1.GND"]),
        # The rails must never meet.
        forbid("VIN", "GND"),
        forbid("+3V3", "GND"),
    ],
)
