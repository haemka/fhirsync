from support import write_and_load_config

# README "common and per-type settings": 'common' applies to all resource types, a type's own setting overrides it.


# README: single values are overridden one by one: Patient gets put (common) with identifier (own)
def test_request_values_are_overridden_one_by_one(tmp_path):
    config = write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
        target:
          mode: file
          outdir: ./out
        bundling:
          resources:
            common:
              request:
                method: put
                url: id
            Patient:
              request:
                url: identifier
            ResearchStudy:
              request:
                method: post
                condition: ifNoneExist
    """)

    assert config.bundling.request_for("Patient").model_dump() == {
        "method": "put", "url": "identifier", "condition": None}
    assert config.bundling.request_for("ResearchStudy").model_dump() == {
        "method": "post", "url": "id", "condition": "ifNoneExist"}
    assert config.bundling.request_for("Consent").model_dump() == {
        "method": "put", "url": "id", "condition": None}


# README: lists are replaced as a whole, Encounter follows all references although common ignores Patient
def test_lists_are_replaced(tmp_path):
    config = write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
        target:
          mode: file
          outdir: ./out
        bundling:
          resources:
            common:
              ignore_references_to: [Patient]
            Encounter:
              ignore_references_to: []
            Consent:
              ignore_references_to: [Patient, Organization]
    """)

    assert config.bundling.ignore_references_to_for("Encounter") == []
    assert config.bundling.ignore_references_to_for("Consent") == ["Patient", "Organization"]
    assert config.bundling.ignore_references_to_for("Observation") == ["Patient"]


# README settings reference: reference_mode per type
def test_reference_mode_per_type(tmp_path):
    config = write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
        target:
          mode: file
          outdir: ./out
        bundling:
          resources:
            Encounter:
              reference_mode: as_is
    """)

    assert config.bundling.reference_mode_for("Encounter") == "as_is"
    assert config.bundling.reference_mode_for("Consent") == "identifier"


# README: search parameters are merged parameter by parameter
def test_search_parameters_are_merged(tmp_path):
    config = write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
          request_params:
            common:
              _count: "10"
              _lastUpdated: ge2025
            Consent:
              _lastUpdated: ge2026
        target:
          mode: file
          outdir: ./out
    """)

    assert config.source.request_params_for("Consent") == {"_count": "10", "_lastUpdated": "ge2026"}
    assert config.source.request_params_for("Patient") == {"_count": "10", "_lastUpdated": "ge2025"}


# config-example.yaml: a common section with all entries commented out is empty, not an error
def test_empty_common_search_parameters(tmp_path):
    config = write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
          request_params:
            common:
              # _lastUpdated: ge2025-03-18
            Consent:
              domain:identifier: MII
        target:
          mode: file
          outdir: ./out
    """)

    assert config.source.request_params_for("Consent") == {"domain:identifier": "MII"}
