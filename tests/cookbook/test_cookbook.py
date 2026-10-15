import datetime
import logging
import os
import sys

import pytest
import yaml
from support import GICS_RULES, apply_rules

from fhirsync.rules import RuleError

# One test per example in docs/rules.md section 7. The rules are copied from the documentation.

CONFIG_EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "config-example.yaml")


# docs/rules.md 7.1: the minimal rule set removes id and meta.lastUpdated, everything else stays
def test_7_1_minimal_rule_set(patient):
    rules = """
        "*":
          finalize:
            - delete: [id, meta.lastUpdated]
    """

    result = apply_rules(rules, patient)

    assert "id" not in result
    assert result["meta"] == {"versionId": "3"}
    assert result["name"] == [{"family": "Muster", "given": ["Erika"]}]


# docs/rules.md 7.2: config-example.yaml contains the gICS rules, the same that the bundling tests use
def test_7_2_config_example_contains_the_gics_rules():
    with open(CONFIG_EXAMPLE, encoding="utf-8") as file:
        example_rules = yaml.safe_load(file)["processing"]["rules"]

    assert example_rules == yaml.safe_load(GICS_RULES)


# docs/rules.md 7.3: inactive Consents are skipped with a warning
def test_7_3_skip_inactive_consent(gics_consent, caplog):
    gics_consent["status"] = "inactive"
    rules = """
        Consent:
          filter:
            - skip:
                when: "status != 'active'"
    """

    result = apply_rules(rules, gics_consent)

    assert result is None
    assert caplog.records[-1].getMessage().endswith("skipped by processing.rules.Consent.filter[0] (status != 'active')")


# docs/rules.md 7.3: active Consents pass unchanged
def test_7_3_keep_active_consent(gics_consent):
    rules = """
        Consent:
          filter:
            - skip:
                when: "status != 'active'"
    """

    result = apply_rules(rules, gics_consent)

    assert result["id"] == gics_consent["id"]
    assert result["patient"] == gics_consent["patient"]
    assert result["provision"] == gics_consent["provision"]


# docs/rules.md 7.3: Observations entered in error are skipped with an info message
def test_7_3_skip_observation_entered_in_error(observation, caplog):
    observation["status"] = "entered-in-error"
    rules = """
        Observation:
          filter:
            - skip:
                when: "status = 'entered-in-error'"
                log: info
    """

    with caplog.at_level(logging.INFO):
        result = apply_rules(rules, observation)

    assert result is None
    assert caplog.records[-1].levelno == logging.INFO


TEST_PATIENT_RULES = """
    Patient:
      filter:
        - skip:
            when: "identifier.where(system = 'http://hospital.example/sid/mrn').value.startsWith('99')"
"""


# docs/rules.md 7.4: the Patient with hospital number 9900001 is skipped
def test_7_4_skip_test_patient(patient):
    patient["identifier"] = [{"system": "http://hospital.example/sid/mrn", "value": "9900001"}]

    result = apply_rules(TEST_PATIENT_RULES, patient)

    assert result is None


# docs/rules.md 7.4: the one with 0012345 passes
def test_7_4_keep_normal_patient(patient):
    result = apply_rules(TEST_PATIENT_RULES, patient)

    assert result is not None


# docs/rules.md 7.5: the pseudonym from the identifier of gICS's Patient becomes the hospital identifier
def test_7_5_pseudonym_from_identifier(gics_patient):
    rules = r"""
        Patient:
          filter:
            - extract:
                name: psn
                from: "identifier.where(system = 'https://ths-greifswald.de/fhir/gics/identifiers/Pseudonym').value"
                pattern: '^\d{10}$'
          transform:
            - set_identifier:
                system: "{identifierSystem('Patient')}"
                value: "{%psn}"
    """

    result = apply_rules(rules, gics_patient)

    assert result["identifier"] == [{"system": "http://hospital.example/sid/patient", "value": "1000000001"}]


# docs/rules.md 7.6: keep gICS's identifier of the ResearchStudy and put the own one first
def test_7_6_keep_gics_identifier(gics_research_study):
    rules = """
        ResearchStudy:
          transform:
            # copy gICS's identifier to the second position, then overwrite the first one
            - set: {path: "identifier[1].system", value: "{identifier[0].system}"}
            - set: {path: "identifier[1].value", value: "{identifier[0].value}"}
            - set: {path: "identifier[0].system", value: "{identifierSystem('ResearchStudy')}"}
            - set: {path: "identifier[0].value", value: "{id}"}
    """

    result = apply_rules(rules, gics_research_study)

    assert result["identifier"] == [
        {"system": "http://hospital.example/sid/consentSource", "value": "468b895a-c809-11f1-8d48-d6456abb6135"},
        {"system": "https://ths-greifswald.de/fhir/gics/", "value": "MII"},
    ]


TAG_RULES = """
    "*":
      transform:
        - set:
            path: "meta.tag[0]"
            value: {system: "http://hospital.example/cs/sync-source", code: gics, display: "Synced from gICS"}
        - set: {path: "meta.tag[1].system", value: "http://hospital.example/cs/sync-date"}
        - set: {path: "meta.tag[1].code", value: "{today()}"}
"""


# docs/rules.md 7.7: a fixed tag and the date of the sync
def test_7_7_tags(gics_consent):
    result = apply_rules(TAG_RULES, gics_consent)

    assert result["meta"]["tag"] == [
        {"system": "http://hospital.example/cs/sync-source", "code": "gics", "display": "Synced from gICS"},
        {"system": "http://hospital.example/cs/sync-date", "code": datetime.date.today().isoformat()},
    ]


# docs/rules.md 7.7: meta.tag[0] replaces the first existing tag
def test_7_7_existing_tag_is_replaced(patient):
    patient["meta"]["tag"] = [{"system": "http://example.org/old", "code": "x"}]

    result = apply_rules(TAG_RULES, patient)

    assert result["meta"]["tag"][0]["system"] == "http://hospital.example/cs/sync-source"


# docs/rules.md 7.8: remove narrative and source metadata, an empty meta is left as {}
def test_7_8_remove_narrative_and_metadata(patient):
    rules = """
        "*":
          finalize:
            - delete: [id, meta.lastUpdated, meta.versionId, text]
    """

    result = apply_rules(rules, patient)

    assert "id" not in result
    assert "text" not in result
    assert result["meta"] == {}


NATURAL_KEY_RULES = """
    Observation:
      transform:
        - set_identifier:
            system: "{identifierSystem('Observation')}"
            value: "{(subject.reference + '|' + code.coding.where(system = 'http://loinc.org').code.first() + '|' + effective.ofType(dateTime).toString()).sha256()}"
"""


# docs/rules.md 7.9: an identifier from patient, LOINC code and time (42f28ceb…d4b4f837)
def test_7_9_identifier_from_natural_key(observation):
    result = apply_rules(NATURAL_KEY_RULES, observation)

    # SHA-256 of 'Patient/p-4711|718-7|2026-03-01T10:00:00+01:00'
    assert result["identifier"] == [{
        "system": "http://hospital.example/sid/observation",
        "value": "42f28ceb0dd50df7c2dacef92d5a9b392f4be4a31d2178c4df7ca370d4b4f837",
    }]


# docs/rules.md 7.9: a missing part of the key stops the run (here: no LOINC code)
def test_7_9_missing_key_part(observation):
    observation["code"] = {"text": "no coding"}

    with pytest.raises(RuleError, match="gives no value"):
        apply_rules(NATURAL_KEY_RULES, observation)


PSEUDONYMISATION_RULES = """
    Patient:
      filter:
        - extract:
            name: mrn
            from: "identifier.where(system = 'http://hospital.example/sid/mrn').value"
            on_mismatch: error
      transform:
        - set_identifier:
            system: "{identifierSystem('Patient')}"
            value: "{('my-secret-salt|' + %mrn).sha256()}"
        - delete: [name, telecom, address, text]
        - set: {path: birthDate, value: "{birthDate.toString().substring(0, 4)}"}
"""


# docs/rules.md 7.10: salted hash of the hospital number (d32b0211…f72b45e09), birth year only, gender kept
def test_7_10_pseudonymise_patient(patient):
    result = apply_rules(PSEUDONYMISATION_RULES, patient)

    # SHA-256 of 'my-secret-salt|0012345'
    assert result["identifier"] == [{
        "system": "http://hospital.example/sid/patient",
        "value": "d32b0211ff3ff9c45549e1e40aba863c3b3fc0f783918e6d7a09d45f72b45e09",
    }]
    assert result["birthDate"] == "1980"
    assert result["gender"] == "female"
    assert "name" not in result
    assert "telecom" not in result
    assert "address" not in result
    assert "text" not in result


# docs/rules.md 7.10: a Patient without hospital number stops the run (on_mismatch: error)
def test_7_10_patient_without_hospital_number(patient):
    patient["identifier"] = []

    with pytest.raises(RuleError, match="for variable mrn"):
        apply_rules(PSEUDONYMISATION_RULES, patient)


CODING_STATUS_RULES = """
    Observation:
      transform:
        - set: {path: "meta.tag[0].system", value: "http://hospital.example/cs/coding-status"}
        - set:
            path: "meta.tag[0].code"
            value: "{iif(code.coding.where(system = 'http://loinc.org').exists(), 'loinc-coded', 'local-code')}"
"""


# docs/rules.md 7.11: an Observation with only a local code gets the tag code local-code ...
def test_7_11_local_code(observation):
    observation["code"] = {"coding": [{"system": "http://hospital.example/cs/lab", "code": "HB"}]}

    result = apply_rules(CODING_STATUS_RULES, observation)

    assert result["meta"]["tag"] == [{"system": "http://hospital.example/cs/coding-status", "code": "local-code"}]


# docs/rules.md 7.11: ... one with a LOINC code gets loinc-coded
def test_7_11_loinc_code(observation):
    result = apply_rules(CODING_STATUS_RULES, observation)

    assert result["meta"]["tag"][0]["code"] == "loinc-coded"


PATIENT_ID_CHECK_RULES = r"""
    Consent:
      filter:
        - extract: {name: pat_id, from: patient.display, pattern: '(\d{10})$', on_mismatch: empty}
      transform:
        - set: {path: "meta.tag[0].system", value: "http://hospital.example/cs/patient-id-check"}
        - set: {path: "meta.tag[0].code", value: "{iif(%pat_id = '', 'missing', 'ok')}"}
"""


# docs/rules.md 7.11: mark Consents without valid patient id instead of skipping them
def test_7_11_mark_missing_patient_id(gics_consent):
    gics_consent["patient"]["display"] = "Pseudonym studypsn42"

    result = apply_rules(PATIENT_ID_CHECK_RULES, gics_consent)

    assert result["meta"]["tag"] == [{"system": "http://hospital.example/cs/patient-id-check", "code": "missing"}]


# docs/rules.md 7.11: Consents with a valid patient id are marked ok
def test_7_11_mark_valid_patient_id(gics_consent):
    result = apply_rules(PATIENT_ID_CHECK_RULES, gics_consent)

    assert result["meta"]["tag"][0]["code"] == "ok"


# lab_mapping.py of docs/rules.md 7.12
LAB_MAPPING = '''
"""Map local laboratory codes to LOINC, skip Observations without a mapping."""
LOINC = {"HB": ("718-7", "Hemoglobin"), "LEUKO": ("6690-2", "Leukocytes")}


def map_local_codes(resource, variables):
    coding = resource["code"]["coding"][0]
    if coding["system"] != "http://hospital.example/cs/lab":
        return resource
    if coding["code"] not in LOINC:
        return None  # skip: no LOINC mapping
    code, display = LOINC[coding["code"]]
    resource["code"]["coding"].insert(0, {"system": "http://loinc.org", "code": code, "display": display})
    return resource
'''

LAB_MAPPING_RULES = """
    Observation:
      filter:
        - python: {function: "lab_mapping:map_local_codes"}
"""


@pytest.fixture
def lab_mapping(tmp_path, monkeypatch):
    # makes lab_mapping.py importable
    (tmp_path / "lab_mapping.py").write_text(LAB_MAPPING)
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("lab_mapping", None)


# docs/rules.md 7.12: HB gets the LOINC code 718-7 as first coding
def test_7_12_map_local_code(lab_mapping, observation):
    observation["code"] = {"coding": [{"system": "http://hospital.example/cs/lab", "code": "HB"}]}

    result = apply_rules(LAB_MAPPING_RULES, observation)

    assert result["code"]["coding"] == [
        {"system": "http://loinc.org", "code": "718-7", "display": "Hemoglobin"},
        {"system": "http://hospital.example/cs/lab", "code": "HB"},
    ]


# docs/rules.md 7.12: an Observation with code XYZ is skipped
def test_7_12_skip_unmapped_code(lab_mapping, observation):
    observation["code"] = {"coding": [{"system": "http://hospital.example/cs/lab", "code": "XYZ"}]}

    result = apply_rules(LAB_MAPPING_RULES, observation)

    assert result is None


# docs/rules.md 7.13: filter still sees the source's reference Patient/p-4711
def test_7_13_value_from_reference(observation):
    rules = """
        Observation:
          filter:
            - extract: {name: patient_id, from: subject.reference, pattern: '^Patient/(.+)$'}
          transform:
            - set: {path: "note[0].text", value: "Source patient {%patient_id}"}
    """

    result = apply_rules(rules, observation)

    assert result["note"] == [{"text": "Source patient p-4711"}]
