from support import write_and_load_config

# README "Configuration": settings without effect are logged as warnings, fhirsync continues.


# README "Configuration": the example warning
def test_outdir_in_server_mode(tmp_path, caplog):
    write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
        target:
          mode: server
          base_url: https://fhirserver.example/fhir
          outdir: ./out
    """)

    assert caplog.messages == [f"Configuration {tmp_path}/config.yaml: target.outdir: ignored, not used in mode server"]


# config-example.yaml: base_url, auth, ca_file and rfc5280 of the target are only used in mode server
def test_server_settings_in_file_mode(tmp_path, caplog):
    write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
        target:
          mode: file
          outdir: ./out
          base_url: https://fhirserver.example/fhir
          ca_file: ./ca.crt
    """)

    assert [message.split(": ", 1)[1] for message in caplog.messages] == [
        "target.base_url: ignored, not used in mode file",
        "target.ca_file: ignored, not used in mode file",
    ]


# README settings reference: target.auth is only used with mode server
def test_target_auth_in_file_mode(tmp_path, caplog):
    write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
        target:
          mode: file
          outdir: ./out
          auth:
            username: user
            password: secret
    """)

    assert caplog.messages[0].endswith("target.auth: ignored, not used in mode file")


# config-example.yaml: the condition is only used with post
def test_condition_with_put(tmp_path, caplog):
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
                method: put
                condition: ifNoneExist
    """)

    assert caplog.messages[0].endswith("bundling.resources.Patient.request.condition: ignored, not used with method put")


# config-example.yaml: the url is only used with put
def test_url_with_post(tmp_path, caplog):
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
                method: post
                url: id
    """)

    assert caplog.messages[0].endswith("bundling.resources.Patient.request.url: ignored, not used with method post")


# an inherited url doesn't cause a warning: common sets url, the type uses post
def test_inherited_url_with_post(tmp_path, caplog):
    write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
        target:
          mode: file
          outdir: ./out
        bundling:
          resources:
            common:
              request:
                url: id
            Patient:
              request:
                method: post
    """)

    assert caplog.messages == []


# README settings reference: username with password is a complete basic auth, no warning
def test_basic_auth_has_no_warning(tmp_path, caplog):
    write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
          auth:
            username: user
            password: secret
        target:
          mode: file
          outdir: ./out
    """)

    assert caplog.messages == []


# README settings reference: OAuth with all four settings, no warning
def test_oauth_has_no_warning(tmp_path, caplog):
    write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
          auth:
            auth_url: https://auth.example
            realm: REALM
            client_id: fhirsync
            client_secret: secret
        target:
          mode: file
          outdir: ./out
    """)

    assert caplog.messages == []


# config-example.yaml: put with url, and post with condition, no warning
def test_request_settings_matching_the_method_have_no_warning(tmp_path, caplog):
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
                method: put
                url: id
            ResearchStudy:
              request:
                method: post
                condition: ifNoneExist
    """)

    assert caplog.messages == []
