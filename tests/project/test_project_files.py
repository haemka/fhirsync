import os
import tomllib

# These tests check README statements about the project itself.

REPO = os.path.join(os.path.dirname(__file__), "..", "..")

with open(os.path.join(REPO, "pyproject.toml"), "rb") as pyproject_file:
    PYPROJECT = tomllib.load(pyproject_file)


# README "Project structure": the listed files and directories exist
def test_project_structure():
    for path in [
        "fhirsync/__init__.py",
        "fhirsync/cli.py",
        "fhirsync/config.py",
        "fhirsync/bundler.py",
        "fhirsync/rules.py",
        "fhirsync/sources.py",
        "docs/rules.md",
        "tests/config",
        "tests/rules",
        "tests/cookbook",
        "tests/bundling",
        "tests/sources",
        "tests/library",
        "tests/cli",
        "tests/connections",
        "tests/project",
        "tests/support.py",
        "tests/conftest.py",
        "tests/data",
        "config-example.yaml",
        "ci/verify_published_package.py",
        ".github/workflows/ci.yml",
        "ARCHITECTURE.md",
    ]:
        assert os.path.exists(os.path.join(REPO, path)), path


# README "Requirements": Python 3.13 or newer
def test_python_version():
    assert PYPROJECT["project"]["requires-python"] == ">=3.13"


# README "Usage": the command is fhirsync
def test_command_name():
    assert PYPROJECT["project"]["scripts"] == {"fhirsync": "fhirsync.cli:run"}


# README "Installation": the extra xml installs dic-helper-toolkit's XML support for reading FHIR XML files
def test_xml_extra():
    assert PYPROJECT["project"]["optional-dependencies"] == {"xml": ["dic-helper-toolkit[xml]>=1.0.4"]}


# README "Installation": uv sync also installs pytest and responses from the dev dependency group
def test_dev_dependency_group():
    names = [requirement.split(">")[0].split("=")[0] for requirement in PYPROJECT["dependency-groups"]["dev"]]

    assert names == ["pytest", "responses"]


# README "Installation": all dependencies come from PyPI, there is no other package index; dic-helper-toolkit is
# required in version 1.0.4 or newer
def test_dependencies_from_pypi():
    assert "uv" not in PYPROJECT.get("tool", {})
    assert "dic-helper-toolkit>=1.0.4" in PYPROJECT["project"]["dependencies"]
