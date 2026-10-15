import pytest
from support import write_and_load_config

from fhirsync.config import ConfigError

# README "Configuration": incomplete settings, invalid values and unknown variables are errors at startup.


# README settings reference: username and password only together
def test_username_without_password(tmp_path):
    with pytest.raises(ConfigError, match="source.auth: username also needs password"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
              auth:
                username: user
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: ... a password needs a username
def test_password_without_username(tmp_path):
    with pytest.raises(ConfigError, match="source.auth: password also needs username"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
              auth:
                password: secret
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: auth_url, realm, client_id and client_secret all or none
def test_oauth_without_client_secret(tmp_path):
    with pytest.raises(ConfigError, match="source.auth: auth_url, realm, client_id also need client_secret"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
              auth:
                auth_url: https://auth.example
                realm: REALM
                client_id: fhirsync
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: ... OAuth without auth_url
def test_oauth_without_auth_url(tmp_path):
    with pytest.raises(ConfigError, match="source.auth: realm, client_id, client_secret also need auth_url"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
              auth:
                realm: REALM
                client_id: fhirsync
                client_secret: secret
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: ... OAuth without realm
def test_oauth_without_realm(tmp_path):
    with pytest.raises(ConfigError, match="source.auth: auth_url, client_id, client_secret also need realm"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
              auth:
                auth_url: https://auth.example
                client_id: fhirsync
                client_secret: secret
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: ... OAuth without client_id
def test_oauth_without_client_id(tmp_path):
    with pytest.raises(ConfigError, match="source.auth: auth_url, realm, client_secret also need client_id"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
              auth:
                auth_url: https://auth.example
                realm: REALM
                client_secret: secret
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: ... a client_secret alone
def test_client_secret_alone(tmp_path):
    with pytest.raises(ConfigError, match="source.auth: client_secret also needs auth_url, realm, client_id"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
              auth:
                client_secret: secret
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: OAuth with username and password (password flow) is valid
def test_oauth_with_username_and_password(tmp_path):
    config = write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
          auth:
            auth_url: https://auth.example
            realm: REALM
            client_id: fhirsync
            client_secret: secret
            username: user
            password: secret
        target:
          mode: file
          outdir: ./out
    """)

    assert config.source.auth.username == "user"


# README settings reference: target.base_url is required with mode server
def test_server_mode_without_base_url(tmp_path):
    with pytest.raises(ConfigError, match="target: mode server needs base_url"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
            target:
              mode: server
        """)


# README settings reference: target.outdir is required with mode file
def test_file_mode_without_outdir(tmp_path):
    with pytest.raises(ConfigError, match="target: mode file needs outdir"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
            target:
              mode: file
        """)


# README settings reference: source is required
def test_missing_source(tmp_path):
    with pytest.raises(ConfigError, match="source: required configuration variable is missing"):
        write_and_load_config(tmp_path, """
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: reference_mode is identifier or as_is
def test_invalid_reference_mode(tmp_path):
    with pytest.raises(ConfigError, match="bundling.resources.common.reference_mode: Input should be 'as_is' or "
                                          "'identifier'"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
            target:
              mode: file
              outdir: ./out
            bundling:
              resources:
                common:
                  reference_mode: fullUrl
        """)


# README settings reference: bundle.type is transaction, batch or collection
def test_invalid_bundle_type(tmp_path):
    with pytest.raises(ConfigError, match="bundling.bundle.type: Input should be 'transaction', 'batch' or "
                                          "'collection'"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
            target:
              mode: file
              outdir: ./out
            bundling:
              bundle:
                type: searchset
        """)


# README "Configuration": the example message for a typo, with a suggestion
def test_typo_with_suggestion(tmp_path):
    with pytest.raises(ConfigError, match="bundling.bundle.sise: unknown configuration variable, "
                                          "did you mean 'size'?"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
            target:
              mode: file
              outdir: ./out
            bundling:
              bundle:
                sise: 2
        """)


# README "Configuration": typos are found at every level, here in request
def test_typo_in_request(tmp_path):
    with pytest.raises(ConfigError, match="bundling.resources.Patient.request.methd: unknown configuration variable, "
                                          "did you mean 'method'?"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
            target:
              mode: file
              outdir: ./out
            bundling:
              resources:
                Patient:
                  request:
                    methd: post
        """)


# README "Configuration": ... in a resource type's settings
def test_typo_in_resource_setting(tmp_path):
    with pytest.raises(ConfigError, match="bundling.resources.Consent.ignore_references: unknown configuration "
                                          "variable, did you mean 'ignore_references_to'?"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
            target:
              mode: file
              outdir: ./out
            bundling:
              resources:
                Consent:
                  ignore_references: [Patient]
        """)


# README "Configuration": ... and in section names
def test_typo_in_section(tmp_path):
    with pytest.raises(ConfigError, match="bundlin: unknown configuration variable, did you mean 'bundling'?"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
            target:
              mode: file
              outdir: ./out
            bundlin:
              bundle:
                size: 2
        """)


# README "Upgrading": the flat bundling settings of the INI format are unknown
def test_old_flat_setting(tmp_path):
    with pytest.raises(ConfigError, match="bundling.type: unknown configuration variable"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
            target:
              mode: file
              outdir: ./out
            bundling:
              type: batch
        """)


# README "Configuration": all problems are reported at once
def test_all_problems_at_once(tmp_path):
    with pytest.raises(ConfigError) as error:
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
              auth:
                auth_url: https://auth.example
                realm: R
                client_id: c
            target:
              mode: file
              outdir: ./out
            bundling:
              bundle:
                sise: 2
        """)

    assert "source.auth: auth_url, realm, client_id also need client_secret" in str(error.value)
    assert "bundling.bundle.sise: unknown configuration variable, did you mean 'size'?" in str(error.value)
