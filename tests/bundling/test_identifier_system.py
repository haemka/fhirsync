import pytest
import responses
from support import entries, read_bundles, write_and_load_config

from fhirsync import ResourceBundler

# These tests check which identifier counts for requests and rewritten references: the first one, or the one with
# the system configured in bundling.resources.<common|Type>.identifier_system (README settings reference, README
# "Important behaviour": the first identifier counts unless identifier_system is configured).

SOURCE = "https://source.example/fhir"
# A Patient with two identifiers: the MRN first, the pseudonym second
PATIENT = {"resourceType": "Patient", "id": "p1",
           "identifier": [{"system": "http://hospital.example/sid/mrn", "value": "0012345"},
                          {"system": "http://hospital.example/sid/pseudonym", "value": "1000000001"}]}
# The conditional reference to it with the first identifier, and with the pseudonym
BY_MRN = "Patient?identifier=http://hospital.example/sid/mrn|0012345"
BY_PSEUDONYM = "Patient?identifier=http://hospital.example/sid/pseudonym|1000000001"


def run(tmp_path, bundling, resources):
    """Search Observation on a mocked server with the bundling section (YAML) and return the written Bundles.

    resources: the source's resources by 'Type/id'; the search finds 'Observation/o1', the others are read when they
    are referenced.
    """
    config = write_and_load_config(tmp_path, f"""
        source:
          base_url: {SOURCE}
        target:
          mode: file
          outdir: {tmp_path}/out
        bundling:
{bundling}
    """)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as source:
        source.add("GET", f"{SOURCE}/Observation", json={"resourceType": "Bundle", "type": "searchset",
                                                         "entry": [{"resource": resources["Observation/o1"]}]})
        for reference, resource in resources.items():
            source.add("GET", f"{SOURCE}/{reference}", json=resource)
        ResourceBundler(config).fetch_resources("Observation")
    return read_bundles(tmp_path / "out")


OBSERVATION = {"resourceType": "Observation", "id": "o1", "status": "final", "code": {"text": "Hb"},
               "identifier": [{"system": "http://hospital.example/sid/observation", "value": "o1"}],
               "subject": {"reference": "Patient/p1"}}


# Without identifier_system the first identifier counts, for the request and for the rewritten reference
def test_first_identifier_by_default(tmp_path):
    bundles = run(tmp_path, "          bundle: {size: 1}", {"Observation/o1": OBSERVATION, "Patient/p1": PATIENT})

    assert entries(bundles, "Patient")[0]["request"]["url"] == BY_MRN
    assert entries(bundles, "Observation")[0]["resource"]["subject"]["reference"] == BY_MRN


# With identifier_system, the identifier with that system counts, also when it isn't the first one
def test_identifier_with_the_configured_system(tmp_path):
    bundles = run(tmp_path, """          resources:
            Patient:
              identifier_system: http://hospital.example/sid/pseudonym""",
                  {"Observation/o1": OBSERVATION, "Patient/p1": PATIENT})

    assert entries(bundles, "Patient")[0]["request"]["url"] == BY_PSEUDONYM
    assert entries(bundles, "Observation")[0]["resource"]["subject"]["reference"] == BY_PSEUDONYM


# The ifNoneExist condition of post uses the identifier with the configured system too
def test_if_none_exist_uses_the_configured_system(tmp_path):
    bundles = run(tmp_path, """          resources:
            Patient:
              identifier_system: http://hospital.example/sid/pseudonym
              request: {method: post, condition: ifNoneExist}""",
                  {"Observation/o1": OBSERVATION, "Patient/p1": PATIENT})

    assert entries(bundles, "Patient")[0]["request"] == {
        "method": "POST", "url": "Patient",
        "ifNoneExist": "identifier=http://hospital.example/sid/pseudonym|1000000001"}


# on_missing_identifier_system error (the default): no identifier with the system stops the run
def test_missing_identifier_system_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="Resource Patient/p1 has no identifier with system http://hospital.example/"
                                         "sid/other. Set bundling.resources.Patient.on_missing_identifier_system to "
                                         "first to use its first identifier instead."):
        run(tmp_path, """          resources:
            Patient:
              identifier_system: http://hospital.example/sid/other""",
            {"Observation/o1": OBSERVATION, "Patient/p1": PATIENT})


# on_missing_identifier_system first: a warning, and the first identifier counts
def test_missing_identifier_system_first(tmp_path, caplog):
    bundles = run(tmp_path, """          resources:
            Patient:
              identifier_system: http://hospital.example/sid/other
              on_missing_identifier_system: first""",
                  {"Observation/o1": OBSERVATION, "Patient/p1": PATIENT})

    assert entries(bundles, "Patient")[0]["request"]["url"] == BY_MRN
    assert ("Resource Patient/p1 has no identifier with system http://hospital.example/sid/other, its first identifier "
            "(http://hospital.example/sid/mrn) is used." in caplog.messages)


# A resource type with a single identifier (QuestionnaireResponse): it counts without identifier_system ...
def test_single_identifier(tmp_path):
    response = {"resourceType": "QuestionnaireResponse", "id": "qr1", "status": "completed",
                "identifier": {"system": "http://hospital.example/sid/qr", "value": "qr1"},
                "subject": {"reference": "Patient/p1"}}
    observation = {**OBSERVATION, "derivedFrom": [{"reference": "QuestionnaireResponse/qr1"}]}

    bundles = run(tmp_path, """          resources:
            common:
              ignore_references_to: [Patient]""",
                  {"Observation/o1": observation, "QuestionnaireResponse/qr1": response})

    assert entries(bundles, "QuestionnaireResponse")[0]["request"]["url"] == \
        "QuestionnaireResponse?identifier=http://hospital.example/sid/qr|qr1"
    assert entries(bundles, "Observation")[0]["resource"]["derivedFrom"] == [
        {"reference": "QuestionnaireResponse?identifier=http://hospital.example/sid/qr|qr1"}]


# ... and with identifier_system it must have that system
def test_single_identifier_with_another_system(tmp_path):
    response = {"resourceType": "QuestionnaireResponse", "id": "qr1", "status": "completed",
                "identifier": {"system": "http://hospital.example/sid/qr", "value": "qr1"}}
    observation = {"resourceType": "Observation", "id": "o1", "status": "final", "code": {"text": "Hb"},
                   "identifier": [{"system": "http://hospital.example/sid/observation", "value": "o1"}],
                   "derivedFrom": [{"reference": "QuestionnaireResponse/qr1"}]}

    with pytest.raises(ValueError, match="Resource QuestionnaireResponse/qr1 has no identifier with system "
                                         "http://hospital.example/sid/other"):
        run(tmp_path, """          resources:
            QuestionnaireResponse:
              identifier_system: http://hospital.example/sid/other""",
            {"Observation/o1": observation, "QuestionnaireResponse/qr1": response})
