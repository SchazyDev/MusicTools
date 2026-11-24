"""Guards against version drift between the app, packaging and installer.

The expected version is derived from ``utils/constants.py`` (single source of
truth), so a release never requires editing this test.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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


def _installer_output_base():
    match = re.search(r"^\s*OutputBaseFilename\s*=\s*(\S+)", _read("installer.iss"), re.MULTILINE)
    assert match, "installer.iss: OutputBaseFilename line not found"
    return match.group(1)


def _expected_version():
    version = _constants_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"utils/constants.py: VERSION {version!r} is not a semantic version"
    )
    return version


def test_pyproject_version_matches_constants():
    expected = _expected_version()
    actual = _pyproject_version()
    assert actual == expected, (
        f"pyproject.toml: project.version is {actual!r}, expected {expected!r} "
        "(from utils/constants.py VERSION)"
    )


def test_installer_version_matches_constants():
    expected = _expected_version()
    actual = _installer_version()
    assert actual == expected, (
        f"installer.iss: AppVersion is {actual!r}, expected {expected!r} "
        "(from utils/constants.py VERSION)"
    )


def test_installer_output_base_embeds_version():
    expected = _expected_version()
    underscored = "_v" + expected.replace(".", "_") + "_setup"
    actual = _installer_output_base()
    assert underscored in actual, (
        f"installer.iss: OutputBaseFilename is {actual!r}, expected it to embed "
        f"{underscored!r} (from utils/constants.py VERSION)"
    )


def test_versions_are_consistent():
    expected = _expected_version()
    sources = {
        "utils/constants.py": _constants_version(),
        "pyproject.toml": _pyproject_version(),
        "installer.iss": _installer_version(),
    }
    mismatched = [f"{name}={version}" for name, version in sources.items()
                  if version != expected]
    assert not mismatched, (
        f"Version drift, expected {expected}: {', '.join(mismatched)}"
    )


def test_changelog_has_release_heading():
    expected = _expected_version()
    changelog = _read("CHANGELOG.md")
    assert f"## [{expected}]" in changelog, (
        f"CHANGELOG.md: missing '## [{expected}]' heading "
        "(from utils/constants.py VERSION)"
    )


def test_test_has_no_hardcoded_version():
    source = Path(__file__).read_text(encoding="utf-8")
    assert not re.search(r'"[1-9]\d*\.[0-9]+\.[0-9]+"', source), (
        "tests/test_version_sync.py: hardcoded version literal found; "
        "derive it from utils/constants.py instead"
    )
