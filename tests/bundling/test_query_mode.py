import json
import os
import re
from urllib.parse import unquote

import pytest
import requests
import responses
from support import write_and_load_config

from fhirsync.bundler import ResourceBundler

# These tests run queries against a mocked source server and check the requests and the written Bundles.
# The source pages are registered in the order they are returned; the mock returns them one after another.

SOURCE = "https://source.example/fhir"


def search_page(*patient_ids, total=None, next_url=None):
    """A search result Bundle with a Patient (with identifier) for every id."""
    page = {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [{"resource": {"resourceType": "Patient", "id": patient_id,
                                "identifier": [{"system": "http://hospital.example/sid/patient",
                                                "value": patient_id}]}}
                  for patient_id in patient_ids],
    }
    if total is not None:
        page["total"] = total
    if next_url:
        page["link"] = [{"relation": "next", "url": next_url}]
    return page


def written_bundles(outdir):
    bundles = []
    for name in sorted(os.listdir(outdir)):
        with open(os.path.join(outdir, name), encoding="utf-8") as file:
            bundles.append(json.load(file))
    return bundles


# README "Queries": a query is a resource type, the parameters configured for it are added
def test_query_with_resource_type_only(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
          request_params:
            Patient:
              active: "true"
        target:
          mode: file
          outdir: {tmp_path}/out
    """)

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Patient", json=search_page("p1"))
        ResourceBundler(config).fetch_resources("Patient")
        requested = [unquote(call.request.url) for call in source.calls]

    assert requested == [f"{SOURCE}/Patient?active=true&_count=50"]


# README "Queries": the query's own parameters take precedence over the configured ones
def test_query_parameters_take_precedence(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
          request_params:
            common:
              _lastUpdated: ge2025
            Patient:
              active: "true"
        target:
          mode: file
          outdir: {tmp_path}/out
    """)

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Patient", json=search_page("p1"))
        ResourceBundler(config).fetch_resources("Patient?active=false&gender=female")
        requested = [unquote(call.request.url) for call in source.calls]

    assert requested == [f"{SOURCE}/Patient?_lastUpdated=ge2025&active=false&gender=female&_count=50"]


# README settings reference: a list value in request_params repeats the parameter
def test_list_value_repeats_the_parameter(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
          request_params:
            Patient:
              _lastUpdated: [ge2025, lt2026]
        target:
          mode: file
          outdir: {tmp_path}/out
    """)

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Patient", json=search_page("p1"))
        ResourceBundler(config).fetch_resources("Patient")
        requested = [unquote(call.request.url) for call in source.calls]

    assert requested == [f"{SOURCE}/Patient?_lastUpdated=ge2025&_lastUpdated=lt2026&_count=50"]


# README "Queries": fhirsync pages through all results following next links
def test_paging_with_next_links(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
        target:
          mode: file
          outdir: {tmp_path}/out
    """)

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Patient", json=search_page("p1", next_url=f"{SOURCE}/Patient?page=2"))
        source.add("GET", f"{SOURCE}/Patient", json=search_page("p2"))
        ResourceBundler(config).fetch_resources("Patient")
        requested = [unquote(call.request.url) for call in source.calls]

    assert requested == [f"{SOURCE}/Patient?_count=50", f"{SOURCE}/Patient?page=2"]
    assert len(written_bundles(tmp_path / "out")) == 2


# README "Queries": with source.next_param, fhirsync pages with offsets. The next page is requested when a page
# is full: fhirsync requests 50 results per page.
def test_offset_paging_with_next_param(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
          next_param: _offset
        target:
          mode: file
          outdir: {tmp_path}/out
    """)
    full_page = search_page(*[f"p{n}" for n in range(50)])

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Patient", json=full_page)
        source.add("GET", f"{SOURCE}/Patient", json=search_page("p50"))
        ResourceBundler(config).fetch_resources("Patient")
        requested = [unquote(call.request.url) for call in source.calls]

    assert requested == [f"{SOURCE}/Patient?_count=50", f"{SOURCE}/Patient?_count=50&_offset=50"]


# README "Queries": offset paging also works with a configured _count, a page is full at that _count.
# (Fixed in dic-helper-toolkit 0.9.10.)
def test_offset_paging_with_configured_count(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
          next_param: _offset
          request_params:
            Patient:
              _count: "2"
        target:
          mode: file
          outdir: {tmp_path}/out
    """)

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Patient", json=search_page("p1", "p2"))
        source.add("GET", f"{SOURCE}/Patient", json=search_page("p3"))
        ResourceBundler(config).fetch_resources("Patient")
        requested = [unquote(call.request.url) for call in source.calls]

    assert requested == [f"{SOURCE}/Patient?_count=2", f"{SOURCE}/Patient?_count=2&_offset=2"]
# README "Upgrading": offset paging is only used when next_param is set; config-example.yaml source.next_param: without it

# README "Upgrading": offset paging is only used when next_param is set; README "Syncing gICS consents": without it
# a search ends after the first page, and a warning is logged if the Bundle's total shows that results are missing
def test_no_offset_paging_without_next_param(tmp_path, caplog):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
        target:
          mode: file
          outdir: {tmp_path}/out
    """)

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Patient", json=search_page(*[f"p{n}" for n in range(50)], total=51))
        ResourceBundler(config).fetch_resources("Patient")
        requested = [unquote(call.request.url) for call in source.calls]

    assert requested == [f"{SOURCE}/Patient?_count=50"]
    assert ("Search for Patient returned 50 of 51 results without a next link. The server probably doesn't support "
            "standard FHIR paging, configure next_param to use offset paging.") in caplog.messages


# README settings reference: bundle.size is the number of search results per Bundle
def test_bundle_size(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
        target:
          mode: file
          outdir: {tmp_path}/out
        bundling:
          bundle:
            size: 2
    """)

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Patient", json=search_page("p1", "p2", "p3"))
        ResourceBundler(config).fetch_resources("Patient")

    sizes = sorted(len(bundle["entry"]) for bundle in written_bundles(tmp_path / "out"))
    assert sizes == [1, 2]


# README settings reference: bundle.size 0 writes one Bundle per search page
def test_bundle_size_zero(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
        target:
          mode: file
          outdir: {tmp_path}/out
        bundling:
          bundle:
            size: 0
    """)

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Patient", json=search_page("p1", "p2", next_url=f"{SOURCE}/Patient?page=2"))
        source.add("GET", f"{SOURCE}/Patient", json=search_page("p3"))
        ResourceBundler(config).fetch_resources("Patient")

    sizes = sorted(len(bundle["entry"]) for bundle in written_bundles(tmp_path / "out"))
    assert sizes == [1, 2]


# README settings reference: the Bundle file is <identifier>.json, Bundle.identifier has the configured system and
# a random UUID as value, and the Bundle has the configured type. config-example.yaml: the outdir is created if missing.
def test_bundle_file_identifier_and_type(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
        target:
          mode: file
          outdir: {tmp_path}/out
        bundling:
          bundle:
            type: batch
            identifier_system: http://hospital.example/sid/bundle
    """)

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Patient", json=search_page("p1"))
        ResourceBundler(config).fetch_resources("Patient")

    # tmp_path/out didn't exist before
    [file_name] = os.listdir(tmp_path / "out")
    [bundle] = written_bundles(tmp_path / "out")
    assert bundle["type"] == "batch"
    assert bundle["identifier"]["system"] == "http://hospital.example/sid/bundle"
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}", bundle["identifier"]["value"])
    assert file_name == bundle["identifier"]["value"] + ".json"


# README "How it works": in mode server every Bundle is posted to the target server
def test_bundles_are_posted_to_the_target(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
        target:
          mode: server
          base_url: https://target.example/fhir
    """)

    with responses.RequestsMock() as servers:
        servers.add("GET", f"{SOURCE}/Patient", json=search_page("p1", "p2"))
        servers.add("POST", "https://target.example/fhir/", json={"resourceType": "Bundle", "type": "transaction-response"})
        ResourceBundler(config).fetch_resources("Patient")
        posted = [json.loads(call.request.body) for call in servers.calls if call.request.method == "POST"]

    assert [bundle["entry"][0]["request"] for bundle in posted] == [
        {"method": "PUT", "url": "Patient?identifier=http://hospital.example/sid/patient|p1"},
        {"method": "PUT", "url": "Patient?identifier=http://hospital.example/sid/patient|p2"},
    ]


# config-example.yaml: the configured search parameters are also added to the search for a reference written as
# Type?..., and take precedence over the reference's own. (Processing such references has a known limitation, see
# docs/rules.md 8; this test only checks the search request. The search finds nothing, which on_missing_reference
# warning allows.)
def test_search_parameters_for_search_references(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
          request_params:
            Patient:
              active: "true"
        target:
          mode: file
          outdir: {tmp_path}/out
        bundling:
          resources:
            common:
              request:
                url: id
              on_missing_reference: warning
    """)
    observation = {"resourceType": "Observation", "id": "o-1", "status": "final", "code": {"text": "Hb"},
                   "subject": {"reference": "Patient?identifier=http://hospital.example/sid/mrn|0012345&active=false"}}

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Observation",
                   json={"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": observation}]})
        source.add("GET", f"{SOURCE}/Patient", json={"resourceType": "Bundle", "type": "searchset"})
        ResourceBundler(config).fetch_resources("Observation")
        requested = [unquote(call.request.url) for call in source.calls]

    assert requested[1] == f"{SOURCE}/Patient?identifier=http://hospital.example/sid/mrn|0012345&active=true&_count=50"


# ARCHITECTURE.md "Error handling": a 404 from the target is logged with the target's OperationOutcome and the run
# continues with the next Bundle
def test_target_404_is_logged_and_the_run_continues(tmp_path, caplog):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
        target:
          mode: server
          base_url: https://target.example/fhir
    """)
    not_found = {"resourceType": "OperationOutcome",
                 "issue": [{"severity": "error", "code": "not-found", "diagnostics": "Patient not found"}]}

    with responses.RequestsMock() as servers:
        servers.add("GET", f"{SOURCE}/Patient", json=search_page("p1", "p2"))
        servers.add("POST", "https://target.example/fhir/", json=not_found, status=404)
        servers.add("POST", "https://target.example/fhir/", json={"resourceType": "Bundle", "type": "transaction-response"})
        ResourceBundler(config).fetch_resources("Patient")
        posts = [call for call in servers.calls if call.request.method == "POST"]

    assert len(posts) == 2
    assert any(message.endswith("Server response: error: Patient not found") for message in caplog.messages)


# ARCHITECTURE.md "Error handling": any other HTTP error from the target ends the run
def test_other_target_errors_end_the_run(tmp_path):
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
        target:
          mode: server
          base_url: https://target.example/fhir
    """)

    with responses.RequestsMock() as servers:
        servers.add("GET", f"{SOURCE}/Patient", json=search_page("p1", "p2"))
        servers.add("POST", "https://target.example/fhir/", status=500)
        with pytest.raises(requests.exceptions.HTTPError, match="500"):
            ResourceBundler(config).fetch_resources("Patient")
