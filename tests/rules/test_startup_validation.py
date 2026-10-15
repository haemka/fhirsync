import pytest
from support import write_and_load_config

from fhirsync.config import ConfigError

# These tests load a whole configuration, like fhirsync does at startup, and check the error messages.


# docs/rules.md 5: the example message of the section
def test_example_message(tmp_path):
    config = """
        source:
          base_url: http://source.example/fhir
        target:
          mode: file
          outdir: ./out
        processing:
          identifier_systems:
            Patient: http://example.org/sid/patient
          rules:
            Consent:
              filter:
                - set:
                    path: id
                    value: x
    """

    with pytest.raises(ConfigError) as error:
        write_and_load_config(tmp_path, config)

    assert "processing.rules.Consent: filter[0]: set is not allowed in phase filter, allowed are skip, extract, " \
           "python" in str(error.value)


# docs/rules.md 5: a typo in a phase, with a suggestion
def test_typo_in_phase(tmp_path):
    config = """
        source:
          base_url: http://source.example/fhir
        target:
          mode: file
          outdir: ./out
        processing:
          identifier_systems:
            Patient: http://example.org/sid/patient
          rules:
            Consent:
              filtr:
                - skip:
    """

    with pytest.raises(ConfigError, match="processing.rules.Consent.filtr: unknown configuration variable, "
                                          "did you mean 'filter'?"):
        write_and_load_config(tmp_path, config)


# docs/rules.md 5: a typo in an operation, with a suggestion
def test_typo_in_operation(tmp_path):
    config = """
        source:
          base_url: http://source.example/fhir
        target:
          mode: file
          outdir: ./out
        processing:
          identifier_systems:
            Patient: http://example.org/sid/patient
          rules:
            Consent:
              transform:
                - sett:
                    path: id
                    value: x
    """

    with pytest.raises(ConfigError, match="did you mean 'set'?"):
        write_and_load_config(tmp_path, config)


# docs/rules.md 5: element paths are checked
def test_invalid_element_path(tmp_path):
    config = """
        source:
          base_url: http://source.example/fhir
        target:
          mode: file
          outdir: ./out
        processing:
          identifier_systems:
            Patient: http://example.org/sid/patient
          rules:
            Consent:
              transform:
                - delete: "a b"
    """

    with pytest.raises(ConfigError, match="invalid element path 'a b'"):
        write_and_load_config(tmp_path, config)


# docs/rules.md 5: regular expressions are checked
def test_invalid_pattern(tmp_path):
    config = """
        source:
          base_url: http://source.example/fhir
        target:
          mode: file
          outdir: ./out
        processing:
          identifier_systems:
            Patient: http://example.org/sid/patient
          rules:
            Consent:
              filter:
                - extract:
                    name: x
                    from: id
                    pattern: '('
    """

    with pytest.raises(ConfigError, match="invalid pattern"):
        write_and_load_config(tmp_path, config)


# docs/rules.md 5: variable names are checked
def test_invalid_variable_name(tmp_path):
    config = """
        source:
          base_url: http://source.example/fhir
        target:
          mode: file
          outdir: ./out
        processing:
          identifier_systems:
            Patient: http://example.org/sid/patient
          rules:
            Consent:
              filter:
                - extract:
                    name: "pat id"
                    from: id
    """

    with pytest.raises(ConfigError, match="invalid variable name 'pat id'"):
        write_and_load_config(tmp_path, config)


# docs/rules.md 5: unknown FHIRPath functions are found
def test_unknown_function(tmp_path):
    config = """
        source:
          base_url: http://source.example/fhir
        target:
          mode: file
          outdir: ./out
        processing:
          identifier_systems:
            Patient: http://example.org/sid/patient
          rules:
            Consent:
              filter:
                - skip:
                    when: "unknownFunction()"
    """

    with pytest.raises(ConfigError, match="Not implemented: unknownFunction"):
        write_and_load_config(tmp_path, config)


# docs/rules.md 5: wrong numbers of arguments are found
def test_wrong_number_of_arguments(tmp_path):
    config = """
        source:
          base_url: http://source.example/fhir
        target:
          mode: file
          outdir: ./out
        processing:
          identifier_systems:
            Patient: http://example.org/sid/patient
          rules:
            Consent:
              filter:
                - skip:
                    when: "status.where()"
    """

    with pytest.raises(ConfigError, match="wrong arity"):
        write_and_load_config(tmp_path, config)


# docs/rules.md 5: undefined variables are found
def test_undefined_variable(tmp_path):
    config = """
        source:
          base_url: http://source.example/fhir
        target:
          mode: file
          outdir: ./out
        processing:
          identifier_systems:
            Patient: http://example.org/sid/patient
          rules:
            Consent:
              filter:
                - skip:
                    when: "%nope = 'x'"
    """

    with pytest.raises(ConfigError, match="undefined environment variable: nope"):
        write_and_load_config(tmp_path, config)


# docs/rules.md 4.4 and 5: identifierSystem with a type that isn't configured
def test_identifier_system_not_configured(tmp_path):
    config = """
        source:
          base_url: http://source.example/fhir
        target:
          mode: file
          outdir: ./out
        processing:
          identifier_systems:
            Patient: http://example.org/sid/patient
          rules:
            Consent:
              transform:
                - set:
                    path: meta.source
                    value: "{identifierSystem('Encounter')}"
    """

    with pytest.raises(ConfigError, match="no identifier system configured for Encounter"):
        write_and_load_config(tmp_path, config)


# docs/rules.md 3.6: python functions are given as module:function
def test_python_function_without_module(tmp_path):
    config = """
        source:
          base_url: http://source.example/fhir
        target:
          mode: file
          outdir: ./out
        processing:
          identifier_systems:
            Patient: http://example.org/sid/patient
          rules:
            Consent:
              filter:
                - python:
                    function: no_module_given
    """

    with pytest.raises(ConfigError, match="module:function"):
        write_and_load_config(tmp_path, config)


# docs/rules.md 5: malformed expressions like name..family are accepted
def test_double_dot_is_accepted(tmp_path):
    config = """
        source:
          base_url: http://source.example/fhir
        target:
          mode: file
          outdir: ./out
        processing:
          identifier_systems:
            Patient: http://example.org/sid/patient
          rules:
            Patient:
              filter:
                - skip:
                    when: "name..family.exists()"
    """

    config = write_and_load_config(tmp_path, config)

    assert "Patient" in config.processing.rules
