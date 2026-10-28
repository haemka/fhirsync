import http.server
import json
import re
import socket
import subprocess
import threading

import pytest

# These tests run the fhirsync command like a user does, and check its exit code and output.


def fhirsync(*arguments, cwd):
    """Run the fhirsync command and return the finished process (exit code, stdout, stderr)."""
    return subprocess.run(["fhirsync", *arguments], cwd=cwd, capture_output=True, text=True, timeout=60)


def unused_port():
    """A local port nothing listens on, so connections to it are refused."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_source_server(search_result):
    """Start a local FHIR server that answers every request with the search result. Returns the server."""
    class Search(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(search_result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/fhir+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # keep the test output clean

    server = http.server.HTTPServer(("127.0.0.1", 0), Search)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def source_server():
    """A local FHIR server whose searches find nothing. Yields its base URL."""
    server = start_source_server({"resourceType": "Bundle", "type": "searchset", "total": 0})
    yield f"http://127.0.0.1:{server.server_port}/fhir"
    server.shutdown()


# README "Exit codes and logging": 0 means success
def test_successful_run(tmp_path, source_server):
    (tmp_path / "config.yaml").write_text(f"""
source:
  base_url: {source_server}
target:
  mode: file
  outdir: ./out
""")

    result = fhirsync("-c", "config.yaml", "-q", "Consent", cwd=tmp_path)

    assert result.returncode == 0


# README "Usage": -c defaults to config.yaml in the current directory
def test_config_defaults_to_config_yaml(tmp_path, source_server):
    (tmp_path / "config.yaml").write_text(f"""
source:
  base_url: {source_server}
target:
  mode: file
  outdir: ./out
""")

    result = fhirsync("-q", "Consent", cwd=tmp_path)

    assert result.returncode == 0


# README "Exit codes and logging": logging goes to standard error, with timestamps, at level INFO, and ends with the
# number of written and rejected Bundles
def test_logging_goes_to_stderr(tmp_path, source_server):
    (tmp_path / "config.yaml").write_text(f"""
source:
  base_url: {source_server}
target:
  mode: file
  outdir: ./out
""")

    result = fhirsync("-q", "Consent", cwd=tmp_path)

    assert result.stdout == ""
    first_line = result.stderr.splitlines()[0]
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d INFO     fhirsync.bundler: Started Processing", first_line)
    last_line = result.stderr.splitlines()[-1]
    assert last_line.endswith("INFO     fhirsync.bundler: Finished Processing: 0 Bundles with 0 entries written, "
                              "0 rejected")


# README "Exit codes and logging": 2 means an invalid command line, here a server source without -q and without
# source.searches
def test_missing_query(tmp_path):
    (tmp_path / "config.yaml").write_text("""
source:
  base_url: https://gics.example/fhir
target:
  mode: file
  outdir: ./out
""")

    result = fhirsync(cwd=tmp_path)

    assert result.returncode == 2
    assert "no search: give -q/--query or configure source.searches in config.yaml" in result.stderr


# README "Usage": without -q, the searches of source.searches are run, one after another
def test_configured_searches(tmp_path, source_server):
    (tmp_path / "config.yaml").write_text(f"""
source:
  base_url: {source_server}
  searches:
    - Consent?domain:identifier=MII
    - ResearchStudy
target:
  mode: file
  outdir: ./out
""")

    result = fhirsync(cwd=tmp_path)

    assert result.returncode == 0
    assert result.stderr.count("fhirsync.bundler: Started Processing") == 2


# README "Usage": -q replaces source.searches for this run
def test_query_replaces_configured_searches(tmp_path, source_server):
    (tmp_path / "config.yaml").write_text(f"""
source:
  base_url: {source_server}
  searches:
    - Consent?domain:identifier=MII
    - ResearchStudy
target:
  mode: file
  outdir: ./out
""")

    result = fhirsync("-q", "Patient", cwd=tmp_path)

    assert result.returncode == 0
    assert result.stderr.count("fhirsync.bundler: Started Processing") == 1


# README "Usage": with a source of type files, fhirsync syncs the resources of the files
def test_files_source(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "patient.json").write_text(
        '{"resourceType": "Patient", "id": "p1", "identifier": [{"system": "http://hospital.example/sid/patient", '
        '"value": "0012345"}]}')
    (tmp_path / "config.yaml").write_text("""
source:
  type: files
  directory: ./input
target:
  mode: file
  outdir: ./out
""")

    result = fhirsync(cwd=tmp_path)

    assert result.returncode == 0
    assert len(list((tmp_path / "out").iterdir())) == 1


# README "Exit codes and logging": -q with a source of type files is an invalid command line
def test_query_with_files_source(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "config.yaml").write_text("""
source:
  type: files
  directory: ./input
target:
  mode: file
  outdir: ./out
""")

    result = fhirsync("-q", "Patient", cwd=tmp_path)

    assert result.returncode == 2
    assert "-q/--query needs a source of type server" in result.stderr


# README "Exit codes and logging": 1 also means a missing reference with on_missing_reference error (the default)
def test_missing_reference(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "observation.json").write_text(
        '{"resourceType": "Observation", "id": "o1", "status": "final", "code": {"text": "Hb"}, '
        '"subject": {"reference": "Patient/p1"}}')
    (tmp_path / "config.yaml").write_text("""
source:
  type: files
  directory: ./input
target:
  mode: file
  outdir: ./out
""")

    result = fhirsync(cwd=tmp_path)

    assert result.returncode == 1
    assert "Traceback (most recent call last)" in result.stderr
    assert "MissingReferenceError: Reference Patient/p1 of Observation/o1 not found in the source" in result.stderr


# README "Exit codes and logging": 2 means an invalid configuration, all problems are logged
def test_invalid_configuration(tmp_path):
    (tmp_path / "config.yaml").write_text("""
source:
  base_url: https://gics.example/fhir
  auth:
    username: user
target:
  mode: file
bundling:
  bundle:
    sise: 2
""")

    result = fhirsync("-q", "Consent", cwd=tmp_path)

    assert result.returncode == 2
    assert "Invalid configuration in config.yaml:" in result.stderr
    assert "source.auth: username also needs password" in result.stderr
    assert "target: mode file needs outdir" in result.stderr
    assert "bundling.bundle.sise: unknown configuration variable, did you mean 'size'?" in result.stderr


# README "Exit codes and logging": a missing configuration file is an invalid configuration
def test_missing_configuration_file(tmp_path):
    result = fhirsync("-c", "missing.yaml", "-q", "Consent", cwd=tmp_path)

    assert result.returncode == 2
    assert "Cannot read configuration file" in result.stderr


# README "Usage": sync mode (-s) was removed in fhirsync 0.8, -s says so
def test_sync_mode_removed(tmp_path):
    result = fhirsync("-s", cwd=tmp_path)

    assert result.returncode == 2
    assert "sync mode (-s) was removed in fhirsync 0.8" in result.stderr


# README "Exit codes and logging": 1 means an error while running, e.g. an unreachable server, with a traceback
def test_unreachable_source(tmp_path):
    (tmp_path / "config.yaml").write_text(f"""
source:
  base_url: http://127.0.0.1:{unused_port()}/fhir
target:
  mode: file
  outdir: ./out
""")

    result = fhirsync("-q", "Consent", cwd=tmp_path)

    assert result.returncode == 1
    assert "Traceback (most recent call last)" in result.stderr
    assert "ConnectionError" in result.stderr


# README "Exit codes and logging": 1 also means a resource without the identifier its request needs
def test_resource_without_identifier(tmp_path):
    server = start_source_server({"resourceType": "Bundle", "type": "searchset",
                                  "entry": [{"resource": {"resourceType": "Patient", "id": "p1"}}]})
    (tmp_path / "config.yaml").write_text(f"""
source:
  base_url: http://127.0.0.1:{server.server_port}/fhir
target:
  mode: file
  outdir: ./out
""")

    result = fhirsync("-q", "Patient", cwd=tmp_path)
    server.shutdown()

    assert result.returncode == 1
    assert "Traceback (most recent call last)" in result.stderr
    assert "ValueError: Resource Patient/p1 has no identifier." in result.stderr
