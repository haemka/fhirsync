import sys

import pytest
from support import apply_rules

from fhirsync.rules import RuleError

# The functions used by the rules in these tests
CUSTOM_RULES_MODULE = '''
def mark_source(resource, variables):
    resource["meta"]["source"] = "patient " + variables["pat_id"]
    return resource

def drop(resource, variables):
    return None

def fail(resource, variables):
    raise KeyError("broken")
'''


@pytest.fixture(autouse=True)
def custom_rules_module(tmp_path, monkeypatch):
    # docs/rules.md 3.6: the module must be importable, e.g. its directory on the PYTHONPATH
    (tmp_path / "custom_rules.py").write_text(CUSTOM_RULES_MODULE)
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("custom_rules", None)


# docs/rules.md 3.6: the function gets the resource and the variables, and returns the resource
def test_function_changes_the_resource(gics_consent):
    rules = r"""
        Consent:
          filter:
            - extract:
                name: pat_id
                from: patient.display
                pattern: '(\d{10})'
          transform:
            - python:
                function: custom_rules:mark_source
    """

    result = apply_rules(rules, gics_consent)

    assert result["meta"]["source"] == "patient 1000000001"


# docs/rules.md 3.6: returning None in filter skips the resource
def test_function_skips_in_filter(gics_consent):
    rules = """
        Consent:
          filter:
            - python:
                function: custom_rules:drop
    """

    result = apply_rules(rules, gics_consent)

    assert result is None


# docs/rules.md 3.6: returning None in the other phases stops the run
def test_function_cannot_skip_in_transform(gics_consent):
    rules = """
        Consent:
          transform:
            - python:
                function: custom_rules:drop
    """

    with pytest.raises(RuleError, match="can only be skipped in the filter phase"):
        apply_rules(rules, gics_consent)


# docs/rules.md 3.6: an exception stops the run with an error naming the rule
def test_function_exception(gics_consent):
    rules = """
        Consent:
          transform:
            - python:
                function: custom_rules:fail
    """

    with pytest.raises(RuleError, match=r"processing.rules.Consent.transform\[0\] failed .*broken"):
        apply_rules(rules, gics_consent)
