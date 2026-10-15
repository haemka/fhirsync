import os
import ssl
from urllib.parse import parse_qs

import responses
from support import DATA, write_and_load_config

from fhirsync.bundler import ResourceBundler

# These tests check how fhirsync connects to the source and target FHIR servers (README settings reference).
# The connections are made by dic-helper-toolkit's FHIRClient; fhirsync passes the configured settings to it.

KEYCLOAK = "https://auth.example"
# Where the OAuth token endpoint is looked up: the OpenID configuration of the realm
DISCOVERY_URL = f"{KEYCLOAK}/realms/REALM/.well-known/openid-configuration"
TOKEN_URL = f"{KEYCLOAK}/realms/REALM/protocol/openid-connect/token"
TOKEN = {"access_token": "the-token", "token_type": "Bearer", "expires_in": 300}


# README settings reference: username and password are basic authentication
def test_basic_authentication(tmp_path):
    config = write_and_load_config(tmp_path, """
        source:
          base_url: https://source.example/fhir
          auth:
            username: user
            password: secret
        target:
          mode: file
          outdir: ./out
    """)

    with responses.RequestsMock() as source:
        source.add("GET", "https://source.example/fhir/Patient", json={"resourceType": "Bundle", "type": "searchset"})
        ResourceBundler(config).fetch_resources("Patient")
        authorization = source.calls[0].request.headers["Authorization"]

    # base64 of 'user:secret'
    assert authorization == "Basic dXNlcjpzZWNyZXQ="


# README settings reference: OAuth without username and password uses the client credentials flow.
# config-example.yaml: the token endpoint is discovered from the realm.
def test_oauth_client_credentials_flow(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: https://source.example/fhir
          auth:
            auth_url: {KEYCLOAK}
            realm: REALM
            client_id: fhirsync
            client_secret: secret
        target:
          mode: file
          outdir: ./out
    """)

    with responses.RequestsMock() as servers:
        servers.add("GET", DISCOVERY_URL, json={"token_endpoint": TOKEN_URL})
        servers.add("POST", TOKEN_URL, json=TOKEN)
        servers.add("GET", "https://source.example/fhir/Patient", json={"resourceType": "Bundle", "type": "searchset"})
        ResourceBundler(config).fetch_resources("Patient")
        token_request = parse_qs(servers.calls[1].request.body)
        search_authorization = servers.calls[2].request.headers["Authorization"]

    assert token_request["grant_type"] == ["client_credentials"]
    assert search_authorization == "Bearer the-token"


# README settings reference: OAuth with username and password uses the password flow
def test_oauth_password_flow(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: https://source.example/fhir
          auth:
            auth_url: {KEYCLOAK}
            realm: REALM
            client_id: fhirsync
            client_secret: secret
            username: user
            password: user-secret
        target:
          mode: file
          outdir: ./out
    """)

    with responses.RequestsMock() as servers:
        servers.add("GET", DISCOVERY_URL, json={"token_endpoint": TOKEN_URL})
        servers.add("POST", TOKEN_URL, json=TOKEN)
        ResourceBundler(config)
        token_request = parse_qs(servers.calls[1].request.body)

    assert token_request["grant_type"] == ["password"]
    assert token_request["username"] == ["user"]


# README settings reference: with a ca_file, only this file is trusted (requests verifies with exactly this file)
def test_ca_file_is_the_only_trusted_file(tmp_path):
    ca_file = os.path.join(DATA, "test_ca.crt")
    config = write_and_load_config(tmp_path, f"""
        common:
          ca_file: {ca_file}
        source:
          base_url: https://source.example/fhir
        target:
          mode: server
          base_url: https://target.example/fhir
    """)

    bundler = ResourceBundler(config)

    assert bundler.source_client.session.verify == ca_file
    assert bundler.target_client.session.verify == ca_file


# README settings reference: rfc5280 false turns the strict certificate checks off.
# config-example.yaml: certificate chain and hostname are still verified.
def test_rfc5280_false(tmp_path):
    config = write_and_load_config(tmp_path, """
        source:
          base_url: https://source.example/fhir
          rfc5280: false
        target:
          mode: file
          outdir: ./out
    """)

    bundler = ResourceBundler(config)

    adapter = bundler.source_client.session.get_adapter("https://source.example/fhir")
    assert not adapter._ssl_context.verify_flags & ssl.VERIFY_X509_STRICT
    assert adapter._ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert adapter._ssl_context.check_hostname is True
