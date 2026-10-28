from urllib.parse import unquote

import pytest
import requests
import responses
from support import entries, read_bundles, write_and_load_config

from fhirsync import MissingReferenceError, ResourceBundler, SyncResult

# These tests run fhirsync with a source of type server against a mocked server: the searches of a run and missing
# references (README settings reference: source.searches; bundling.resources on_missing_reference).

SOURCE = "https://source.example/fhir"
OBSERVATION = {"resourceType": "Observation", "id": "o1", "status": "final", "code": {"text": "Hb"},
               "identifier": [{"system": "http://hospital.example/sid/observation", "value": "o1"}],
               "subject": {"reference": "Patient/p1"}}
OBSERVATIONS = {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": OBSERVATION}]}
EMPTY = {"resourceType": "Bundle", "type": "searchset"}


def server_config(tmp_path, source="", bundling=""):
    """A configuration with the mocked server as source and Bundles written to tmp_path/out.

    source: more settings of the source section (YAML, indented by 2). bundling: the bundling section (YAML).
    """
    return write_and_load_config(tmp_path, f"""
source:
  base_url: {SOURCE}
{source}
target:
  mode: file
  outdir: {tmp_path}/out
bundling:
{bundling or "  bundle: {size: 1}"}
""")


# run() runs every search of source.searches, one after another, and adds up the results
def test_run_searches(tmp_path):
    config = server_config(tmp_path, source="""  searches:
    - Observation?code=718-7
    - Patient
  request_params:
    Patient:
      active: "true"
""", bundling="""  resources:
    common:
      ignore_references_to: [Patient]""")

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Observation", json=OBSERVATIONS)
        source.add("GET", f"{SOURCE}/Patient", json=EMPTY)
        result = ResourceBundler(config).run()
        requested = [unquote(call.request.url) for call in source.calls]

    assert requested == [f"{SOURCE}/Observation?code=718-7&_count=50", f"{SOURCE}/Patient?active=true&_count=50"]
    assert result == SyncResult(bundles=1, entries=1, rejected=0)


# Searches given to run() (like -q) replace source.searches
def test_run_with_searches_instead_of_the_configured_ones(tmp_path):
    config = server_config(tmp_path, source="  searches: [Patient]", bundling="""  resources:
    common:
      ignore_references_to: [Patient]""")

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Observation", json=OBSERVATIONS)
        ResourceBundler(config).run(["Observation"])
        requested = [unquote(call.request.url) for call in source.calls]

    assert requested == [f"{SOURCE}/Observation?_count=50"]


# A server source without searches has nothing to run
def test_run_without_searches(tmp_path):
    with pytest.raises(ValueError, match="No searches: configure source.searches or give a search."):
        ResourceBundler(server_config(tmp_path)).run()


# on_missing_reference error (the default): a referenced resource the server answers with 404 stops the run
def test_missing_reference_is_an_error(tmp_path):
    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Observation", json=OBSERVATIONS)
        source.add("GET", f"{SOURCE}/Patient/p1", status=404)
        with pytest.raises(MissingReferenceError, match="Reference Patient/p1 of Observation/o1 not found in the "
                                                        "source"):
            ResourceBundler(server_config(tmp_path)).fetch_resources("Observation")


# on_missing_reference warning: the missing reference is logged and left unchanged, the run continues
def test_missing_reference_warning(tmp_path, caplog):
    config = server_config(tmp_path, bundling="""  resources:
    common:
      on_missing_reference: warning""")

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Observation", json=OBSERVATIONS)
        source.add("GET", f"{SOURCE}/Patient/p1", status=404)
        result = ResourceBundler(config).fetch_resources("Observation")

    assert result == SyncResult(bundles=1, entries=1, rejected=0)
    observation = entries(read_bundles(tmp_path / "out"), "Observation")[0]["resource"]
    assert observation["subject"] == {"reference": "Patient/p1"}
    assert ("Reference Patient/p1 of Observation/o1 not found in the source, the reference is left unchanged."
            in caplog.messages)


# README "Upgrading from fhirsync 0.8 and 0.9": a reference written as Type?<parameters> whose search finds nothing is missing too, an error by
# default (before, it was silently ignored)
def test_empty_search_reference_is_an_error(tmp_path):
    observation = {**OBSERVATION, "subject": {"reference": "Patient?identifier=http://hospital.example/sid/mrn|1"}}

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Observation",
                   json={"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": observation}]})
        source.add("GET", f"{SOURCE}/Patient", json=EMPTY)
        with pytest.raises(MissingReferenceError, match=r"Reference Patient\?identifier=http://hospital.example/sid/"
                                                        r"mrn\|1 of Observation/o1 not found in the source"):
            ResourceBundler(server_config(tmp_path)).fetch_resources("Observation")


# Other HTTP errors while reading a reference are not 'missing', they end the run as before
def test_other_http_errors_end_the_run(tmp_path):
    config = server_config(tmp_path, bundling="""  resources:
    common:
      on_missing_reference: warning""")

    with responses.RequestsMock() as source:
        source.add("GET", f"{SOURCE}/Observation", json=OBSERVATIONS)
        source.add("GET", f"{SOURCE}/Patient/p1", status=500)
        with pytest.raises(requests.exceptions.HTTPError, match="500"):
            ResourceBundler(config).fetch_resources("Observation")
