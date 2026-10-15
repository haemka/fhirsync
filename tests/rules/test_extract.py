import logging

import pytest
from support import apply_rules

from fhirsync.rules import RuleError

# In these tests, the extracted variable is written to meta.source, so the test can check it.


# docs/rules.md 3.2: the first result of 'from' is used
def test_extract_uses_first_result(gics_consent):
    # category.coding.code gives 57016-8, consent-status, 2.16.840...
    rules = """
        Consent:
          filter:
            - extract:
                name: code
                from: category.coding.code
          transform:
            - set:
                path: meta.source
                value: "{%code}"
    """

    result = apply_rules(rules, gics_consent)

    assert result["meta"]["source"] == "57016-8"


# docs/rules.md 3.2: the result is converted to text
def test_extract_converts_to_text(gics_consent):
    rules = """
        Consent:
          filter:
            - extract:
                name: count
                from: provision.provision.count()
          transform:
            - set:
                path: meta.source
                value: "x{%count}"
    """

    result = apply_rules(rules, gics_consent)

    assert result["meta"]["source"] == "x27"


# docs/rules.md 3.2: with a pattern, the first capture group is the value
def test_extract_pattern_first_group(gics_consent):
    # patient.display is 'Pseudonym 1000000001'
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
                value: "{%pat_id}"
    """

    result = apply_rules(rules, gics_consent)

    assert result["meta"]["source"] == "1000000001"


# docs/rules.md 3.2: without a group, the whole match is the value; the pattern is searched, not matched in full
def test_extract_pattern_whole_match(gics_consent):
    rules = r"""
        Consent:
          filter:
            - extract:
                name: digits
                from: patient.display
                pattern: '\d{4}'
          transform:
            - set:
                path: meta.source
                value: "{%digits}"
    """

    result = apply_rules(rules, gics_consent)

    assert result["meta"]["source"] == "1000"


# docs/rules.md 3.2: anchor the pattern with ^...$ if the whole value has to match
def test_extract_anchored_pattern(gics_consent):
    # 'Pseudonym 1000000001' is not only 10 digits, so this doesn't match
    rules = r"""
        Consent:
          filter:
            - extract:
                name: pat_id
                from: patient.display
                pattern: '^\d{10}$'
    """

    result = apply_rules(rules, gics_consent)

    assert result is None


# docs/rules.md 3.2: on_mismatch: skip (the default) logs a warning and skips the resource
def test_extract_on_mismatch_skip(gics_consent, caplog):
    gics_consent["patient"]["display"] = "Pseudonym studypsn42"
    rules = r"""
        Consent:
          filter:
            - extract:
                name: pat_id
                from: patient.display
                pattern: '(\d{10})'
    """

    result = apply_rules(rules, gics_consent)

    assert result is None
    assert caplog.records[-1].levelno == logging.WARNING
    assert "Skipping the resource" in caplog.records[-1].getMessage()


# docs/rules.md 3.2: on_mismatch: error stops the run
def test_extract_on_mismatch_error(gics_consent):
    gics_consent["patient"]["display"] = "Pseudonym studypsn42"
    rules = r"""
        Consent:
          filter:
            - extract:
                name: pat_id
                from: patient.display
                pattern: '(\d{10})'
                on_mismatch: error
    """

    with pytest.raises(RuleError, match="for variable pat_id"):
        apply_rules(rules, gics_consent)


# docs/rules.md 3.2: on_mismatch: empty continues with an empty value
def test_extract_on_mismatch_empty(gics_consent):
    gics_consent["patient"]["display"] = "Pseudonym studypsn42"
    rules = r"""
        Consent:
          filter:
            - extract:
                name: pat_id
                from: patient.display
                pattern: '(\d{10})'
                on_mismatch: empty
          transform:
            - set:
                path: meta.source
                value: "x{%pat_id}x"
    """

    result = apply_rules(rules, gics_consent)

    assert result["meta"]["source"] == "xx"


# docs/rules.md 3.2: the variable can be used in later filter rules
def test_variable_in_later_filter_rule(gics_consent):
    rules = r"""
        Consent:
          filter:
            - extract:
                name: pat_id
                from: patient.display
                pattern: '(\d{10})'
            - skip:
                when: "%pat_id = '1000000001'"
    """

    result = apply_rules(rules, gics_consent)

    assert result is None
