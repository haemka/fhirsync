import pytest
from pydantic import ValidationError
from support import apply_rules

from fhirsync.rules import RuleError


# docs/rules.md 4.6: paths use the JSON names of choice elements: valueQuantity ...
def test_path_with_json_name_of_choice_element(observation):
    rules = """
        Observation:
          transform:
            - set:
                path: valueQuantity.value
                value: "14"
    """

    result = apply_rules(rules, observation)

    assert result["valueQuantity"]["value"] == 14


# docs/rules.md 4.6: ... not value
def test_path_with_base_name_of_choice_element(observation):
    rules = """
        Observation:
          transform:
            - set:
                path: value
                value: "14"
    """

    with pytest.raises(RuleError, match="no valid resource"):
        apply_rules(rules, observation)


# docs/rules.md 4.6: an index equal to the length of the list appends
def test_index_equal_to_length_appends(patient):
    patient["meta"]["profile"] = ["http://example.org/a"]
    rules = """
        Patient:
          transform:
            - set:
                path: meta.profile[1]
                value: http://example.org/b
    """

    result = apply_rules(rules, patient)

    assert result["meta"]["profile"] == ["http://example.org/a", "http://example.org/b"]


# docs/rules.md 4.6: an existing index replaces the entry
def test_existing_index_replaces(patient):
    patient["meta"]["profile"] = ["http://example.org/a"]
    rules = """
        Patient:
          transform:
            - set:
                path: meta.profile[0]
                value: http://example.org/b
    """

    result = apply_rules(rules, patient)

    assert result["meta"]["profile"] == ["http://example.org/b"]


# docs/rules.md 4.6: an index beyond the end is an error
def test_index_beyond_the_end(patient):
    patient["meta"]["profile"] = ["http://example.org/a"]
    rules = """
        Patient:
          transform:
            - set:
                path: meta.profile[5]
                value: http://example.org/b
    """

    with pytest.raises(RuleError, match="index out of range"):
        apply_rules(rules, patient)


# docs/rules.md 4.6: no where, first() or other functions in paths
@pytest.mark.parametrize("path", ["identifier.where(system = 'a')", "name.first()"])
def test_functions_in_paths_are_rejected(patient, path):
    rules = f"""
        Patient:
          transform:
            - delete: "{path}"
    """

    with pytest.raises(ValidationError, match="invalid element path"):
        apply_rules(rules, patient)


# docs/rules.md 4.6: an unknown element makes the resource invalid, which stops the run
def test_unknown_element_stops(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: unknownElement
                value: x
    """

    with pytest.raises(RuleError, match="Patient/p-4711 is no valid resource after the transform rules"):
        apply_rules(rules, patient)


# docs/rules.md 4.6: so does a malformed date
def test_malformed_date_stops(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: birthDate
                value: not a date
    """

    with pytest.raises(RuleError, match="Patient/p-4711 is no valid resource after the transform rules"):
        apply_rules(rules, patient)
