"""The published version string must not drift between the five places that carry it."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pyproject() -> str:
    return re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)


def test_version_is_consistent():
    declared = _pyproject()
    package = re.search(r'__version__ = "([^"]+)"',
                        (ROOT / "multisimforge" / "__init__.py").read_text(encoding="utf-8")).group(1)
    adapter = re.search(r'version="([^"]+)"',
                        (ROOT / "mcp_adapter" / "server.py").read_text(encoding="utf-8")).group(1)
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))["version"]
    changelog = re.search(r"^## \[([0-9.]+)\]", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M).group(1)
    assert {declared, package, adapter, server, changelog} == {declared}, (
        f"version mismatch: pyproject={declared} package={package} adapter={adapter} "
        f"server.json={server} changelog={changelog}")


def test_package_reports_its_version():
    import multisimforge
    assert multisimforge.__version__ == _pyproject()
