import pytest
import yaml
from pydantic import ValidationError
from support import apply_rules


# docs/rules.md 2: each rule has exactly one operation, two are an error
def test_rule_with_two_operations(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: id
                value: x
              delete: text
    """

    with pytest.raises(ValidationError, match="exactly one operation"):
        apply_rules(rules, patient)


# docs/rules.md 2: ... and none is an error too
def test_rule_without_operation(patient):
    rules = """
        Patient:
          transform:
            - {}
    """

    with pytest.raises(ValidationError, match="exactly one operation"):
        apply_rules(rules, patient)


# docs/rules.md 1: set is not allowed in the filter phase
def test_operation_not_allowed_in_phase(patient):
    rules = """
        Patient:
          filter:
            - set:
                path: id
                value: x
    """

    with pytest.raises(ValidationError, match="set is not allowed in phase filter"):
        apply_rules(rules, patient)


# docs/rules.md 1: skip is only allowed in the filter phase
def test_skip_not_allowed_in_transform(patient):
    rules = """
        Patient:
          transform:
            - skip:
    """

    with pytest.raises(ValidationError, match="skip is not allowed in phase transform"):
        apply_rules(rules, patient)


# docs/rules.md 2: "*" rules run first, then the rules of the type, top to bottom (the last set wins)
def test_order_of_rules(patient):
    rules = """
        "*":
          transform:
            - set:
                path: meta.source
                value: http://example.org/common
        Patient:
          transform:
            - set:
                path: meta.source
                value: http://example.org/first
            - set:
                path: meta.source
                value: http://example.org/second
    """

    result = apply_rules(rules, patient)

    assert result["meta"]["source"] == "http://example.org/second"


# docs/rules.md 8: there is no conditional set
def test_set_has_no_condition(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: gender
                value: other
                when: "true"
    """

    with pytest.raises(ValidationError, match="when"):
        apply_rules(rules, patient)


# docs/rules.md 2: "*" must be quoted in YAML
def test_star_must_be_quoted():
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load("*:\n  finalize: []\n")

    assert yaml.safe_load('"*":\n  finalize: []\n') == {"*": {"finalize": []}}


# docs/rules.md 2: in inline mappings, a value with [ must be quoted
def test_brackets_in_inline_mappings():
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load("set: {path: meta.profile[0], value: x}")

    assert yaml.safe_load('set: {path: "meta.profile[0]", value: x}') == {
        "set": {"path": "meta.profile[0]", "value": "x"}}


# docs/rules.md 2: in block form, values starting with { [ * & ! % @ need quotes
@pytest.mark.parametrize("first_character", ["{", "[", "*", "&", "!", "%", "@"])
def test_special_first_characters(first_character):
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(f"value: {first_character}id}} and more")


# docs/rules.md 3.2: regular expressions in single quotes need no escaping
def test_regular_expression_in_single_quotes():
    assert yaml.safe_load(r"pattern: '^\d{10}$'") == {"pattern": r"^\d{10}$"}


# docs/rules.md 3.2: in double quotes every backslash would have to be doubled
def test_regular_expression_in_double_quotes():
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(r'pattern: "^\d{10}$"')

    assert yaml.safe_load(r'pattern: "^\\d{10}$"') == {"pattern": r"^\d{10}$"}
