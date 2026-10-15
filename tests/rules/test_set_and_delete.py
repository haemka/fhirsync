from support import apply_rules


# docs/rules.md 3.3: missing elements are created, here meta and the meta.profile list
def test_set_creates_missing_elements(observation):
    # the Observation has no meta
    rules = """
        Observation:
          transform:
            - set:
                path: meta.profile[0]
                value: http://example.org/profile
    """

    result = apply_rules(rules, observation)

    assert result["meta"]["profile"] == ["http://example.org/profile"]


# docs/rules.md 3.3: the example of the section, a text value is a template
def test_set_template_value(gics_consent):
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


# docs/rules.md 3.3: a boolean value is set as written
def test_set_literal_boolean(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: active
                value: true
    """

    result = apply_rules(rules, patient)

    assert result["active"] is True


# docs/rules.md 3.3: a mapping value is set as written
def test_set_literal_mapping(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: meta.tag[0]
                value: {system: "http://hospital.example/cs/sync-source", code: gics, display: "Synced from gICS"}
    """

    result = apply_rules(rules, patient)

    assert result["meta"]["tag"] == [
        {"system": "http://hospital.example/cs/sync-source", "code": "gics", "display": "Synced from gICS"}
    ]


# docs/rules.md 3.3: text inside a mapping is not a template
def test_text_inside_mapping_is_no_template(patient):
    rules = """
        Patient:
          transform:
            - set:
                path: meta.tag[0]
                value: {system: "http://hospital.example/cs/x", code: "{id}"}
    """

    result = apply_rules(rules, patient)

    assert result["meta"]["tag"][0]["code"] == "{id}"


# docs/rules.md 3.4: delete one path
def test_delete_one_path(patient):
    rules = """
        Patient:
          transform:
            - delete: text
    """

    result = apply_rules(rules, patient)

    assert "text" not in result


# docs/rules.md 3.4: delete several paths
def test_delete_several_paths(patient):
    rules = """
        Patient:
          transform:
            - delete: [id, meta.lastUpdated, meta.versionId, text]
    """

    result = apply_rules(rules, patient)

    assert "id" not in result
    assert "text" not in result
    assert result["meta"] == {}


# docs/rules.md 3.4: a path that doesn't exist is ignored
def test_delete_missing_path(patient):
    rules = """
        Patient:
          transform:
            - delete: [contained, "name[0].prefix", "address[5]"]
    """

    result = apply_rules(rules, patient)

    assert result["name"] == [{"family": "Muster", "given": ["Erika"]}]
    assert result["address"] == [{"city": "Jena", "postalCode": "07743"}]


# docs/rules.md 3.4: deleting a list entry moves the following entries up
def test_delete_list_entry(patient):
    patient["identifier"] = [
        {"system": "a", "value": "1"},
        {"system": "b", "value": "2"},
        {"system": "c", "value": "3"},
    ]
    rules = """
        Patient:
          transform:
            - delete: identifier[1]
    """

    result = apply_rules(rules, patient)

    assert result["identifier"] == [{"system": "a", "value": "1"}, {"system": "c", "value": "3"}]
