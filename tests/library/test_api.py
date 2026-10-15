import subprocess
import sys
from typing import Optional
from urllib.parse import unquote

import pytest
import responses
from support import write_and_load_config

import fhirsync
from fhirsync import ConfigError, ConfigModel, ResourceBundler, SyncResult, load_yaml

# These tests use fhirsync like an application does (README "Using fhirsync as a library").

SOURCE = "https://source.example/fhir"
# A search result with two Patients that have identifiers
TWO_PATIENTS = {"resourceType": "Bundle", "type": "searchset", "entry": [
    {"resource": {"resourceType": "Patient", "id": "p1",
                  "identifier": [{"system": "http://hospital.example/sid/patient", "value": "p1"}]}},
    {"resource": {"resourceType": "Patient", "id": "p2",
                  "identifier": [{"system": "http://hospital.example/sid/patient", "value": "p2"}]}},
]}


# README "Using fhirsync as a library": the public API is importable from the package
def test_public_api():
    assert fhirsync.__all__ == ["Config", "ConfigError", "ConfigModel", "ResourceBundler", "RuleError", "SyncResult",
                                "load_config", "load_yaml"]


# README "Using fhirsync as a library": importing fhirsync doesn't configure logging, that is up to the application
def test_import_does_not_configure_logging():
    result = subprocess.run([sys.executable, "-c", "import logging, fhirsync, fhirsync.cli\n"
                                                   "print(logging.getLogger().handlers)\n"
                                                   "print(logging.getLogger('fhirsync.bundler').level)"],
                            capture_output=True, text=True, timeout=60)

    # no handlers on the root logger, and no level set for fhirsync's loggers (0 is NOTSET)
    assert result.stdout == "[]\n0\n"


# README "Using fhirsync as a library": fetch_resources() returns the number of Bundles and entries written
def test_result_of_a_run(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
        target:
          mode: file
          outdir: {tmp_path}/out
    """)

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Patient", json=TWO_PATIENTS)
        result = ResourceBundler(config).fetch_resources("Patient")

    # bundle.size 1: one Bundle per search result
    assert result == SyncResult(bundles=2, entries=2, rejected=0)


# README "Using fhirsync as a library": one bundler can run many searches, each returns its own result
def test_one_bundler_many_searches(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
        target:
          mode: file
          outdir: {tmp_path}/out
    """)
    bundler = ResourceBundler(config)

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Patient", json=TWO_PATIENTS)
        source.add("GET", f"{SOURCE}/Patient", json={"resourceType": "Bundle", "type": "searchset"})
        first = bundler.fetch_resources("Patient?identifier=a")
        second = bundler.fetch_resources("Patient?identifier=b")

    assert first == SyncResult(bundles=2, entries=2, rejected=0)
    assert second == SyncResult(bundles=0, entries=0, rejected=0)


# README "Using fhirsync as a library": Bundles the target rejects with 404 are counted as rejected
def test_result_counts_rejected_bundles(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
        target:
          mode: server
          base_url: https://target.example/fhir
    """)

    with responses.RequestsMock() as servers:
        servers.add("GET", f"{SOURCE}/Patient", json=TWO_PATIENTS)
        servers.add("POST", "https://target.example/fhir/", status=404)
        servers.add("POST", "https://target.example/fhir/", json={"resourceType": "Bundle", "type": "transaction-response"})
        result = ResourceBundler(config).fetch_resources("Patient")

    assert result == SyncResult(bundles=1, entries=1, rejected=1)


# README "Using fhirsync as a library": search parameters can be given as a dict, not URL encoded, a list repeats the
# parameter. They take precedence over the query's parameters, which take precedence over source.request_params.
def test_search_parameters_as_dict(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
          request_params:
            Patient:
              active: "true"
              gender: female
        target:
          mode: file
          outdir: {tmp_path}/out
    """)

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Patient", json={"resourceType": "Bundle", "type": "searchset"})
        ResourceBundler(config).fetch_resources("Patient?gender=male&birthdate=ge1980", params={
            "birthdate": ["ge1990", "lt2000"],
            "identifier": "http://hospital.example/sid/patient|p1",
        })
        requested = unquote(source.calls[0].request.url)

    assert requested == (f"{SOURCE}/Patient?active=true&gender=male&birthdate=ge1990&birthdate=lt2000"
                         "&identifier=http://hospital.example/sid/patient|p1&_count=50")


class Broker(ConfigModel):
    """A configuration section of an application, for the load_yaml() tests."""
    server: str
    port: Optional[int] = None
    tls_enabled: bool = False

    def ignored_settings(self):
        return [("port", "example reason")] if self.port and not self.tls_enabled else []


class Application(ConfigModel):
    broker: Broker


# README "Using fhirsync as a library": load_yaml() loads an application's own configuration like fhirsync's,
# with environment variables ...
def test_load_yaml_with_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("BROKER", "mqtt.example")
    (tmp_path / "app.yaml").write_text("broker:\n  server: ${BROKER}\n")

    config = load_yaml(str(tmp_path / "app.yaml"), Application)

    assert config.broker.server == "mqtt.example"


# ... all problems at once, with suggestions for typos ...
def test_load_yaml_errors(tmp_path):
    (tmp_path / "app.yaml").write_text("broker:\n  srever: mqtt.example\n")

    with pytest.raises(ConfigError) as error:
        load_yaml(str(tmp_path / "app.yaml"), Application)

    assert str(error.value) == (f"Invalid configuration in {tmp_path}/app.yaml:\n"
                                "  broker.server: required configuration variable is missing\n"
                                "  broker.srever: unknown configuration variable, did you mean 'server'?")


# ... and warnings for settings without effect, from ignored_settings()
def test_load_yaml_warnings(tmp_path, caplog):
    (tmp_path / "app.yaml").write_text("broker:\n  server: mqtt.example\n  port: 1883\n")

    load_yaml(str(tmp_path / "app.yaml"), Application)

    assert caplog.messages == [f"Configuration {tmp_path}/app.yaml: broker.port: ignored, example reason"]
