from __future__ import annotations

from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--kicad-source",
        action="store",
        default=None,
        help="Path to a KiCad source checkout (master) to verify API assumptions against.",
    )


@pytest.fixture(scope="session")
def kicad_source(request: pytest.FixtureRequest) -> Path:
    raw = request.config.getoption("--kicad-source")
    if not raw:
        pytest.skip("needs --kicad-source=<path to kicad checkout>")
    path = Path(str(raw)).expanduser()
    if not (path / "pcbnew" / "api").is_dir():
        pytest.fail(f"{path} does not look like a KiCad checkout (no pcbnew/api)")
    return path
