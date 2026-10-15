import pytest
from pydantic import ValidationError
from support import apply_rules

from fhirsync.rules import RuleError


# docs/rules.md 4.3: variables belong to one resource type, another type can't use them (error at startup)
def test_variable_of_another_type(gics_consent):
    rules = """
        Consent:
          filter:
            - extract:
                name: pat_id
                from: id
        ResearchStudy:
          transform:
            - set:
                path: title
                value: "{%pat_id}"
    """

    with pytest.raises(ValidationError, match="undefined environment variable: pat_id"):
        apply_rules(rules, gics_consent)


# docs/rules.md 4.3: variables are not available in finalize (error at startup)
def test_variable_in_finalize(gics_consent):
    rules = """
        Consent:
          filter:
            - extract:
                name: pat_id
                from: id
          finalize:
            - set:
                path: meta.source
                value: "{%pat_id}"
    """

    with pytest.raises(ValidationError, match=r"processing.rules.Consent.finalize\[0\]"):
        apply_rules(rules, gics_consent)


# docs/rules.md 4.3: a variable used above its extract is only noticed when the rule runs
def test_variable_used_before_extract(patient):
    rules = """
        Patient:
          filter:
            - skip:
                when: "%pat_id = 'x'"
            - extract:
                name: pat_id
                from: id
    """

    with pytest.raises(RuleError, match="undefined environment variable: pat_id"):
        apply_rules(rules, patient)


# docs/rules.md 3.2 and 4.3: variables are available in transform
def test_variable_in_transform(gics_consent):
    rules = r"""
        Consent:
          filter:
            - extract:
                name: pat_id
                from: patient.display
                pattern: '(\d{10})'
          transform:
            - set:
                path: meta.source
                value: "patient {%pat_id}"
    """

    result = apply_rules(rules, gics_consent)

    assert result["meta"]["source"] == "patient 1000000001"


# docs/rules.md 4.3: a variable set by an extract in the "*" rules can be used in a type's rules
def test_variable_from_star_rules(patient):
    rules = """
        "*":
          filter:
            - extract:
                name: source_id
                from: id
        Patient:
          transform:
            - set:
                path: meta.source
                value: "source {%source_id}"
    """

    result = apply_rules(rules, patient)

    assert result["meta"]["source"] == "source p-4711"
