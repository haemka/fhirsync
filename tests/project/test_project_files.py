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
        "docs/rules.md",
        "tests/config",
        "tests/rules",
        "tests/cookbook",
        "tests/bundling",
        "tests/library",
        "tests/cli",
        "tests/connections",
        "tests/project",
        "tests/support.py",
        "tests/conftest.py",
        "tests/data",
        "config-example.yaml",
        "ARCHITECTURE.md",
    ]:
        assert os.path.exists(os.path.join(REPO, path)), path


# README "Requirements": Python 3.12 or newer
def test_python_version():
    assert PYPROJECT["project"]["requires-python"] == ">=3.12"


# README "Usage": the command is fhirsync
def test_command_name():
    assert PYPROJECT["project"]["scripts"] == {"fhirsync": "fhirsync.cli:run"}


# README "Installation": uv sync also installs pytest and responses from the dev dependency group
def test_dev_dependency_group():
    names = [requirement.split(">")[0].split("=")[0] for requirement in PYPROJECT["dependency-groups"]["dev"]]

    assert names == ["pytest", "responses"]


# README "Installation": dic-helper-toolkit comes from the GitLab index named gitlab
def test_gitlab_index():
    indexes = [index["name"] for index in PYPROJECT["tool"]["uv"]["index"]]

    assert "gitlab" in indexes
    assert PYPROJECT["tool"]["uv"]["sources"]["dic-helper-toolkit"] == {"index": "gitlab"}
