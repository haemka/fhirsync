import os

import pytest
from support import write_and_load_config

from fhirsync.config import ConfigError, load_config

CONFIG_EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "config-example.yaml")


# README "Configuration": a configuration with all sections
def test_full_configuration(tmp_path, caplog):
    config = write_and_load_config(tmp_path, """
        common:
          ca_file: ./ca.crt
        source:
          base_url: https://gics.example/fhir
          auth: {auth_url: https://auth.example, realm: R, client_id: c, client_secret: s}
          next_param: _offset
          request_params:
            Consent:
              domain:identifier: MII
        target:
          mode: server
          base_url: https://fhirserver.example/fhir
          auth: {username: u, password: p}
        bundling:
          bundle: {type: transaction, size: 1, identifier_system: http://example.org/bundles}
          resources:
            common:
              request: {method: put, url: identifier}
              ignore_references_to: [Patient]
            ResearchStudy:
              request: {method: post, condition: ifNoneExist}
        processing:
          identifier_systems:
            Patient: http://example.org/sid/patient
    """)

    assert caplog.records == []  # no warnings
    assert config.source.next_param == "_offset"
    assert config.target.base_url == "https://fhirserver.example/fhir"


# README settings reference: the defaults of a minimal configuration
def test_defaults(tmp_path):
    config = write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
        target:
          mode: file
          outdir: ./out
    """)

    assert config.common.ca_file is None
    assert config.common.rfc5280 is True
    assert config.source.auth is None
    assert config.source.next_param is None
    assert config.source.request_params == {}
    assert config.bundling.bundle.type == "transaction"
    assert config.bundling.bundle.size == 1
    assert config.bundling.bundle.identifier_system == "http://www.uniklinikum-jena.de/fhir/sid/consentBundle"
    assert config.bundling.request_for("Consent").method == "put"
    assert config.bundling.request_for("Consent").url == "identifier"
    assert config.bundling.request_for("Consent").condition is None
    assert config.bundling.reference_mode_for("Consent") == "identifier"
    assert config.bundling.ignore_references_to_for("Consent") == []
    assert config.processing.identifier_systems == {}
    assert config.processing.rules == {}


# README settings reference: source and target fall back to common.ca_file and common.rfc5280
def test_tls_settings_fall_back_to_common(tmp_path):
    config = write_and_load_config(tmp_path, """
        common:
          ca_file: ./common.crt
          rfc5280: false
        source:
          base_url: https://gics.example/fhir
          ca_file: ./source.crt
        target:
          mode: server
          base_url: https://fhirserver.example/fhir
          rfc5280: true
    """)

    assert config.tls(config.source) == ("./source.crt", False)
    assert config.tls(config.target) == ("./common.crt", True)


# config-example.yaml is a valid configuration (its secrets come from environment variables)
def test_config_example_is_valid(monkeypatch):
    monkeypatch.setenv("GICS_CLIENT_SECRET", "secret")
    monkeypatch.setenv("FHIRSERVER_PASSWORD", "secret")

    config = load_config(CONFIG_EXAMPLE)

    assert list(config.processing.rules) == ["Consent", "ResearchStudy", "*"]


# README "Secrets and environment variables": ${NAME} is replaced by the variable
def test_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("GICS_URL", "https://gics.example/fhir")

    config = write_and_load_config(tmp_path, """
        source:
          base_url: ${GICS_URL}
        target:
          mode: file
          outdir: ./out
    """)

    assert config.source.base_url == "https://gics.example/fhir"


# README "Secrets and environment variables": ${NAME:-default} uses the default if the variable isn't set
def test_environment_variable_default(tmp_path, monkeypatch):
    monkeypatch.delenv("GICS_URL", raising=False)

    config = write_and_load_config(tmp_path, """
        source:
          base_url: ${GICS_URL:-https://default.example/fhir}
        target:
          mode: file
          outdir: ./out
    """)

    assert config.source.base_url == "https://default.example/fhir"


# README "Secrets and environment variables": an unset variable without default is an error naming it
def test_environment_variable_not_set(tmp_path, monkeypatch):
    monkeypatch.delenv("GICS_URL", raising=False)

    with pytest.raises(ConfigError, match="source.base_url: environment variable GICS_URL is not set"):
        write_and_load_config(tmp_path, """
            source:
              base_url: ${GICS_URL}
            target:
              mode: file
              outdir: ./out
        """)


# README "Secrets and environment variables": values are substituted after reading, so they may contain anything
def test_environment_variable_with_special_characters(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET", "a: b # c {d}")

    config = write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
          auth:
            username: user
            password: ${SECRET}
        target:
          mode: file
          outdir: ./out
    """)

    assert config.source.auth.password == "a: b # c {d}"


# a missing configuration file is an error
def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="Cannot read configuration file"):
        load_config(str(tmp_path / "missing.yaml"))


# invalid YAML is an error
def test_invalid_yaml(tmp_path):
    with pytest.raises(ConfigError, match="Invalid YAML"):
        write_and_load_config(tmp_path, "source: [unclosed\n")


# a file that isn't a YAML mapping is an error
def test_not_a_mapping(tmp_path):
    with pytest.raises(ConfigError, match="must contain a YAML mapping"):
        write_and_load_config(tmp_path, "- a list\n")


# README "Secrets and environment variables": inside inline mappings ({ … }), values with ${…} must be quoted ...
def test_unquoted_variable_in_inline_mapping(tmp_path):
    with pytest.raises(ConfigError, match="Invalid YAML"):
        write_and_load_config(tmp_path, """
            source: {base_url: ${GICS_URL}}
            target: {mode: file, outdir: ./out}
        """)


# ... quoted, they work
def test_quoted_variable_in_inline_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("GICS_URL", "https://gics.example/fhir")

    config = write_and_load_config(tmp_path, """
        source: {base_url: "${GICS_URL}"}
        target: {mode: file, outdir: ./out}
    """)

    assert config.source.base_url == "https://gics.example/fhir"
