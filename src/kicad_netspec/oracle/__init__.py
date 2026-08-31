"""The only seam through which KiCad reaches this package (DECISIONS D4)."""

from kicad_netspec.oracle.base import (
    Capabilities,
    EnvironmentError_,
    Finding,
    Oracle,
    RuleReport,
)
from kicad_netspec.oracle.cli10 import Cli10Backend
from kicad_netspec.oracle.discovery import KiCadCli, KiCadNotFound, find_kicad_cli

__all__ = [
    "Capabilities",
    "Cli10Backend",
    "EnvironmentError_",
    "Finding",
    "KiCadCli",
    "KiCadNotFound",
    "Oracle",
    "RuleReport",
    "find_kicad_cli",
]
