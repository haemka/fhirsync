"""
Verify the distribution files in a local dist directory against a GitLab PyPI package registry.

Default mode: every local file must be listed in the registry's simple index, its advertised SHA-256 must match the
local file, and the file re-downloaded from the registry must hash to the same value. The downloaded copy then
replaces the local file, so later jobs only ever use the verified published artifact.

--assert-absent: fail if any of the local files is already listed in the registry (used before publishing a release).

Authenticates with CI_JOB_TOKEN via basic auth as gitlab-ci-token.
"""
import argparse
import hashlib
import os
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin

import requests


class _LinkParser(HTMLParser):
    """Collects the links of a PEP 503 simple index page as {link text (file name): href}."""

    def __init__(self):
        """Initialize the parser with no links collected."""
        super().__init__()
        self.links = {}
        self._href = None

    def handle_starttag(self, tag, attrs):
        """Remember the href of an opening <a> tag until its text is read.

        :param tag: The tag name.
        :param attrs: The tag's attributes as (name, value) pairs.
        """
        if tag == "a":
            self._href = dict(attrs).get("href")

    def handle_data(self, data):
        """Store the remembered href under the link text, the file name.

        :param data: The text inside the current tag.
        """
        if self._href is not None and data.strip():
            self.links[data.strip()] = self._href
            self._href = None

    def handle_endtag(self, tag):
        """Forget the href when an <a> tag closes without text.

        :param tag: The tag name.
        """
        if tag == "a":
            self._href = None


def _sha256(data: bytes) -> str:
    """The SHA-256 of the data as a hexadecimal string.

    :param data: The data to hash.
    :return: The hexadecimal digest.
    """
    return hashlib.sha256(data).hexdigest()


def _dist_files(dist_dir: Path) -> list:
    """The wheels and sdists in a dist directory, sorted.

    :param dist_dir: The directory uv build wrote to.
    :return: The paths of the .whl and .tar.gz files.
    """
    # uv build also places a .gitignore into dist/, so only pick up actual distributions.
    return sorted(p for p in dist_dir.iterdir() if p.is_file() and p.name.endswith((".whl", ".tar.gz")))


def _project_name(path: Path) -> str:
    """The normalized project name of a distribution file, as used in the simple index URL.

    :param path: A wheel or sdist.
    :return: The project name, e.g. dic-helper-toolkit.
    """
    # Wheel and sdist filenames both start with the normalized project name, e.g. dic_helper_toolkit-1.0.0-...
    return path.name.split("-")[0].replace("_", "-").lower()


def _index_links(session: requests.Session, simple_url: str, project: str) -> dict:
    """The files the registry lists for a project.

    :param session: The authenticated session.
    :param simple_url: The simple index URL of the registry.
    :param project: The normalized project name.
    :return: {file name: absolute URL, including the #sha256= fragment}; empty if the project is not published yet.
    """
    index_url = f"{simple_url.rstrip('/')}/{project}/"
    response = session.get(index_url)
    if response.status_code == 404:
        return {}
    response.raise_for_status()
    parser = _LinkParser()
    parser.feed(response.text)
    return {name: urljoin(index_url, href) for name, href in parser.links.items()}


def main() -> int:
    """Run the check given on the command line.

    :return: The exit code: 0 if the check passed, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("simple_url", help="Simple index URL of the registry, e.g. .../packages/pypi/simple")
    parser.add_argument("dist_dir", type=Path, help="Directory holding the locally built wheel and sdist")
    parser.add_argument("--assert-absent", action="store_true", help="Fail if any local file is already published")
    args = parser.parse_args()

    local_files = _dist_files(args.dist_dir)
    if not local_files:
        print(f"ERROR: no wheel or sdist in {args.dist_dir}")
        return 1

    session = requests.Session()
    session.auth = ("gitlab-ci-token", os.environ["CI_JOB_TOKEN"])
    links = _index_links(session, args.simple_url, _project_name(local_files[0]))

    if args.assert_absent:
        existing = [p.name for p in local_files if p.name in links]
        if existing:
            print(f"ERROR: already published in the registry: {', '.join(existing)}")
            print("Bump pyproject.toml's version before merging into the default branch.")
            return 1
        print("None of the files are published yet")
        return 0

    for path in local_files:
        if path.name not in links:
            print(f"ERROR: {path.name} not found in the registry index")
            return 1
        url, fragment = urldefrag(links[path.name])
        advertised = fragment.removeprefix("sha256=") if fragment.startswith("sha256=") else None
        local = _sha256(path.read_bytes())

        response = session.get(url)
        response.raise_for_status()
        downloaded = _sha256(response.content)

        if advertised is not None and advertised != local:
            print(f"ERROR: {path.name}: registry advertises sha256 {advertised}, local build is {local}")
            return 1
        if downloaded != local:
            print(f"ERROR: {path.name}: downloaded sha256 {downloaded}, local build is {local}")
            return 1

        path.write_bytes(response.content)
        print(f"Verified published artifact checksum OK: {path.name} sha256={downloaded}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
