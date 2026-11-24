"""Guards against version drift between the app, packaging and installer."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "1.0.0"


def _read(relative_path):
    path = ROOT / relative_path
    assert path.is_file(), f"Missing file {relative_path}"
    return path.read_text(encoding="utf-8")


def _constants_version():
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', _read("utils/constants.py"), re.MULTILINE)
    assert match, "utils/constants.py: VERSION assignment not found"
    return match.group(1)


def _pyproject_version():
    data = tomllib.loads(_read("pyproject.toml"))
    version = data.get("project", {}).get("version")
    assert version, "pyproject.toml: [project] version not found"
    return version


def _installer_version():
    match = re.search(r"^\s*AppVersion\s*=\s*(\S+)", _read("installer.iss"), re.MULTILINE)
    assert match, "installer.iss: AppVersion line not found"
    return match.group(1)


def test_constants_version_is_expected():
    actual = _constants_version()
    assert actual == EXPECTED_VERSION, (
        f"utils/constants.py: VERSION is {actual!r}, expected {EXPECTED_VERSION!r}"
    )


def test_pyproject_version_is_expected():
    actual = _pyproject_version()
    assert actual == EXPECTED_VERSION, (
        f"pyproject.toml: project.version is {actual!r}, expected {EXPECTED_VERSION!r}"
    )


def test_installer_version_is_expected():
    actual = _installer_version()
    assert actual == EXPECTED_VERSION, (
        f"installer.iss: AppVersion is {actual!r}, expected {EXPECTED_VERSION!r}"
    )


def test_versions_are_consistent():
    sources = {
        "utils/constants.py": _constants_version(),
        "pyproject.toml": _pyproject_version(),
        "installer.iss": _installer_version(),
    }
    mismatched = [f"{name}={version}" for name, version in sources.items()
                  if version != EXPECTED_VERSION]
    assert not mismatched, (
        f"Version drift, expected {EXPECTED_VERSION}: {', '.join(mismatched)}"
    )


def test_changelog_has_release_heading():
    changelog = _read("CHANGELOG.md")
    assert "## [1.0.0]" in changelog, "CHANGELOG.md: missing '## [1.0.0]' heading"
