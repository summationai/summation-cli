"""Guard that the package exposes a parseable __version__.

``sum_cli/__init__.py`` is the single authoritative version; ``pyproject.toml``
derives from it via ``[tool.hatch.version]``.
"""

from __future__ import annotations

import re
from pathlib import Path

from sum_cli import __version__

_INIT = Path(__file__).resolve().parents[1] / "sum_cli" / "__init__.py"
_INIT_VERSION = re.compile(r"^__version__\s*=\s*\"([^\"]+)\"", re.MULTILINE)


def test_version_is_semver_like() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__


def test_imported_version_matches_init_file() -> None:
    match = _INIT_VERSION.search(_INIT.read_text())
    assert match, f"No __version__ assignment found in {_INIT}"
    assert __version__ == match.group(1)
