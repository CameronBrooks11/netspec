"""Verify PCB connectivity against declared engineering intent, using KiCad as the oracle."""

__all__ = ["__version__"]

__version__ = "0.0.1"

from kicad_netspec.contract import Spec, forbid, net, polarity

__all__ += ["Spec", "forbid", "net", "polarity"]
