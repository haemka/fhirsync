import logging

from support import apply_rules


# docs/rules.md 3.1: the resource is skipped if 'when' is true
def test_skip_when_condition_is_true(patient):
    rules = """
        Patient:
          filter:
            - skip:
                when: "gender = 'female'"
    """

    result = apply_rules(rules, patient)

    assert result is None


# docs/rules.md 3.1: false keeps the resource
def test_keep_when_condition_is_false(patient):
    rules = """
        Patient:
          filter:
            - skip:
                when: "gender = 'male'"
    """

    result = apply_rules(rules, patient)

    assert result is not None


# docs/rules.md 3.1: an empty result keeps the resource
def test_keep_when_condition_is_empty(patient):
    rules = """
        Patient:
          filter:
            - skip:
                when: "extension.where(url = 'x').value"
    """

    result = apply_rules(rules, patient)

    assert result is not None


# docs/rules.md 3.1: without 'when' the resource is always skipped, written as '- skip:'
def test_skip_without_condition(patient):
    rules = """
        Patient:
          filter:
            - skip:
    """

    result = apply_rules(rules, patient)

    assert result is None


# docs/rules.md 3.1: ... or as '- skip: {}'
def test_skip_with_empty_mapping(patient):
    rules = """
        Patient:
          filter:
            - skip: {}
    """

    result = apply_rules(rules, patient)

    assert result is None


# docs/rules.md 3.1: by default a warning is logged
def test_skip_logs_warning_by_default(patient, caplog):
    rules = """
        Patient:
          filter:
            - skip:
                when: "gender = 'female'"
    """

    apply_rules(rules, patient)

    assert caplog.records[-1].levelno == logging.WARNING
    assert caplog.records[-1].getMessage() == \
        "Patient/p-4711 skipped by processing.rules.Patient.filter[0] (gender = 'female')"


# docs/rules.md 3.1: log: info
def test_skip_logs_info(patient, caplog):
    rules = """
        Patient:
          filter:
            - skip:
                log: info
    """

    with caplog.at_level(logging.INFO):
        apply_rules(rules, patient)

    assert caplog.records[-1].levelno == logging.INFO


# docs/rules.md 3.1: log: none
def test_skip_logs_nothing(patient, caplog):
    rules = """
        Patient:
          filter:
            - skip:
                log: none
    """

    with caplog.at_level(logging.DEBUG, logger="fhirsync.rules"):
        apply_rules(rules, patient)

    assert [r for r in caplog.records if r.name == "fhirsync.rules"] == []
