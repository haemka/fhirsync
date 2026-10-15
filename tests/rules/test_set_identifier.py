import pytest
from support import apply_rules

from fhirsync.rules import RuleError


# docs/rules.md 3.5: the example of the section, a hash of domain, policy and patient id
def test_set_identifier_example(gics_consent):
    rules = r"""
        Consent:
          filter:
            - extract:
                name: pat_id
                from: patient.display
                pattern: '(\d{10})'
          transform:
            - set_identifier:
                system: "{identifierSystem('Consent')}"
                value: "{(organization[0].display + '+' + policy[0].uri + '+' + %pat_id).sha256()}"
    """

    result = apply_rules(rules, gics_consent)

    # SHA-256 of 'MII+2.16.840.1.113883.3.1937.777.24.2.184+1000000001'
    assert result["identifier"] == [{
        "system": "http://hospital.example/sid/consent",
        "value": "bb1ec5b6b37ab84b2ff63d5c7adcabd5622bb375bd6bb7c530773dd03051505b",
    }]


# docs/rules.md 3.5: all existing identifiers are replaced
def test_set_identifier_replaces_identifiers(patient):
    # the patient has the identifier http://hospital.example/sid/mrn|0012345
    rules = """
        Patient:
          transform:
            - set_identifier:
                system: http://example.org/new
                value: "{id}"
    """

    result = apply_rules(rules, patient)

    assert result["identifier"] == [{"system": "http://example.org/new", "value": "p-4711"}]


# docs/rules.md 3.5: resource types with only one identifier get a single element, e.g. Bundle
def test_set_identifier_single_identifier():
    bundle = {"resourceType": "Bundle", "type": "collection"}
    rules = """
        Bundle:
          transform:
            - set_identifier:
                system: http://example.org/bundles
                value: b1
    """

    result = apply_rules(rules, bundle)

    assert result["identifier"] == {"system": "http://example.org/bundles", "value": "b1"}


# docs/rules.md 3.5: a resource type without identifier stops the run, e.g. Binary
def test_set_identifier_without_identifier_element():
    binary = {"resourceType": "Binary", "contentType": "text/plain"}
    rules = """
        Binary:
          transform:
            - set_identifier:
                system: http://example.org/binaries
                value: b1
    """

    with pytest.raises(RuleError, match="Binary has no identifier element"):
        apply_rules(rules, binary)
