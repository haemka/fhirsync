import pytest
from support import apply_rules

from fhirsync.rules import RuleError


# docs/rules.md 4.5: the example of the section
def test_template_example(gics_consent):
    rules = r"""
        Consent:
          filter:
            - extract:
                name: pat_id
                from: patient.display
                pattern: '(\d{10})'
          transform:
            - set:
                path: patient.reference
                value: "Patient?identifier={identifierSystem('Patient')}|{%pat_id}"
    """

    result = apply_rules(rules, gics_consent)

    assert result["patient"]["reference"] == "Patient?identifier=http://hospital.example/sid/patient|1000000001"


# docs/rules.md 4.5: only the first result is used
def test_template_uses_first_result(gics_consent):
    rules = """
        Consent:
          transform:
            - set:
                path: meta.source
                value: "{category.coding.code}"
    """

    result = apply_rules(rules, gics_consent)

    assert result["meta"]["source"] == "57016-8"


# docs/rules.md 4.5: an expression without a result stops the run
def test_template_without_result(gics_consent):
    rules = """
        Consent:
          transform:
            - set:
                path: meta.source
                value: "{organization[3].display}"
    """

    with pytest.raises(RuleError) as error:
        apply_rules(rules, gics_consent)

    assert str(error.value).startswith(
        "processing.rules.Consent.transform[0]: '{organization[3].display}' gives no value for Consent/")


# docs/rules.md 4.5: an alternative for an optional value with iif
def test_template_alternative_with_iif(gics_consent):
    rules = """
        Consent:
          transform:
            - set:
                path: meta.source
                value: "{iif(organization[3].display.exists(), organization[3].display, 'none')}"
    """

    result = apply_rules(rules, gics_consent)

    assert result["meta"]["source"] == "none"


# docs/rules.md 4.5: numbers are written as text
def test_template_number(observation):
    rules = """
        Observation:
          transform:
            - set:
                path: meta.source
                value: "{valueQuantity.value}"
    """

    result = apply_rules(rules, observation)

    assert result["meta"]["source"] == "13.2"


# docs/rules.md 4.5: dates are written as in the resource
def test_template_date(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: meta.source
                value: "{birthDate}"
    """

    result = apply_rules(rules, patient)

    assert result["meta"]["source"] == "1980-05-17"


# docs/rules.md 4.5: booleans are written as True and False
def test_template_boolean(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: meta.source
                value: "{gender = 'female'}"
    """

    result = apply_rules(rules, patient)

    assert result["meta"]["source"] == "True"


# docs/rules.md 4.5: iif gives FHIR's spelling true/false
def test_template_boolean_with_iif(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: meta.source
                value: "{iif(gender = 'female', 'true', 'false')}"
    """

    result = apply_rules(rules, patient)

    assert result["meta"]["source"] == "true"


# docs/rules.md 4.5: literal braces are written doubled
def test_template_literal_braces(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: meta.source
                value: "{{not a template}}"
    """

    result = apply_rules(rules, patient)

    assert result["meta"]["source"] == "{not a template}"


# docs/rules.md 4.5: results are converted to the element's type, leniently for booleans
@pytest.mark.parametrize("text, expected", [
    ("false", False),
    ("False", False),
    ("yes", True),
    ("1", True),
])
def test_template_converted_to_boolean(patient, text, expected):
    rules = f"""
        Patient:
          transform:
            - set:
                path: active
                value: "{text}"
    """

    result = apply_rules(rules, patient)

    assert result["active"] is expected


# docs/rules.md 4.5: 13.2 sets a decimal
def test_template_converted_to_decimal(observation):
    rules = """
        Observation:
          transform:
            - set:
                path: valueQuantity.value
                value: "13.2"
    """

    result = apply_rules(rules, observation)

    assert result["valueQuantity"]["value"] == 13.2


# docs/rules.md 4.5: text that can't be converted stops the run, e.g. maybe for a boolean
def test_template_not_convertible_to_boolean(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: active
                value: maybe
    """

    with pytest.raises(RuleError, match="no valid resource after the transform rules"):
        apply_rules(rules, patient)


# docs/rules.md 4.5: ... or abc for a decimal
def test_template_not_convertible_to_decimal(observation):
    rules = """
        Observation:
          transform:
            - set:
                path: valueQuantity.value
                value: abc
    """

    with pytest.raises(RuleError, match="no valid resource after the transform rules"):
        apply_rules(rules, observation)


# docs/rules.md 4.4: identifierSystem('Patient') gives the configured system
def test_identifier_system_function(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: meta.source
                value: "{identifierSystem('Patient')}"
    """

    result = apply_rules(rules, patient)

    assert result["meta"]["source"] == "http://hospital.example/sid/patient"


# docs/rules.md 4.4: sha256() hashes its input
def test_sha256_function(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: meta.source
                value: "{'abc'.sha256()}"
    """

    result = apply_rules(rules, patient)

    # SHA-256 of 'abc'
    assert result["meta"]["source"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


# docs/rules.md 4.2 and 8: + with a missing part gives no result, in a template that stops the run
def test_plus_with_missing_part(patient):
    # the patient's name has no prefix
    rules = """
        Patient:
          transform:
            - set:
                path: meta.source
                value: "{name.family + '-' + name.prefix}"
    """

    with pytest.raises(RuleError, match="gives no value"):
        apply_rules(rules, patient)


# docs/rules.md 4.2 and 8: & treats a missing part as empty text
def test_ampersand_with_missing_part(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: meta.source
                value: "{name.family & '-' & name.prefix}"
    """

    result = apply_rules(rules, patient)

    assert result["meta"]["source"] == "Muster-"
