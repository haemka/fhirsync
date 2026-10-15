import logging

from support import GICS_RULES, SOURCE_BASE_URL, entries, run_bundler

# These tests run the bundler on recorded gICS responses: four consent-status Consents (pseudonyms 1000000001,
# 1000000002, 1000000003 and the invalid studypsn42), each referencing the MII ResearchStudy and a gICS Patient.


# docs/rules.md introduction: without rules no resource is changed, id and meta.lastUpdated of the source included
def test_without_rules_resources_are_unchanged(tmp_path):
    # request.url id, because gICS Consents have no identifier (docs/rules.md 6)
    bundling = """
        resources:
          common:
            request:
              url: id
            ignore_references_to: [Patient]
    """

    bundles, _ = run_bundler(tmp_path, bundling=bundling)

    consents = [entry["resource"] for entry in entries(bundles, "Consent")]
    assert len(consents) == 4
    for consent in consents:
        assert consent["id"]
        assert consent["meta"]["lastUpdated"]
        assert consent["patient"]["display"].startswith("Pseudonym ")


# docs/rules.md 7.2: with the gICS rules, three Consents are synced, studypsn42 is skipped
def test_gics_rules(tmp_path):
    bundling = """
        resources:
          common:
            ignore_references_to: [Patient]
    """

    bundles, _ = run_bundler(tmp_path, rules=GICS_RULES, bundling=bundling)

    assert len(bundles) == 3
    assert len(entries(bundles, "Consent")) == 3
    assert len(entries(bundles, "ResearchStudy")) == 3


# docs/rules.md 1: an OperationOutcome in a search result is logged and dropped
def test_operation_outcome_is_dropped(tmp_path, caplog):
    search_result = {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [{"resource": {
            "resourceType": "OperationOutcome",
            "issue": [{"severity": "error", "code": "processing", "diagnostics": "test issue"}],
        }}],
    }

    bundles, _ = run_bundler(tmp_path, query="Consent?x=outcome", extra_responses=[
        (f"{SOURCE_BASE_URL}/Consent?x=outcome&_count=50", search_result),
    ])

    assert bundles == []
    assert "OperationOutcome: error - test issue" in caplog.messages


# docs/rules.md 1: a resource skipped in filter is left out, its references aren't even fetched
def test_skipped_resource_references_are_not_fetched(tmp_path):
    rules = """
        Consent:
          filter:
            - skip:
                log: none
    """

    bundles, requested_urls = run_bundler(tmp_path, rules=rules)

    assert bundles == []
    # only the search, no ResearchStudy or Patient
    assert len(requested_urls) == 1
    assert "/Consent?" in requested_urls[0]


# docs/rules.md 1: the request can use the source id (request.url id), finalize removes it afterwards
def test_finalize_runs_after_the_request_is_built(tmp_path):
    bundling = """
        resources:
          common:
            ignore_references_to: [Patient]
          ResearchStudy:
            request:
              url: id
    """

    bundles, _ = run_bundler(tmp_path, rules=GICS_RULES, bundling=bundling)

    study = entries(bundles, "ResearchStudy")[0]
    assert study["request"] == {"method": "PUT", "url": "ResearchStudy/468b895a-c809-11f1-8d48-d6456abb6135"}
    assert "id" not in study["resource"]


# docs/rules.md 6: without the finalize rule the source id stays in the resources
def test_source_id_stays_without_finalize_rule(tmp_path):
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
                value: "{%pat_id}"
        ResearchStudy:
          transform:
            - set_identifier:
                system: "{identifierSystem('ResearchStudy')}"
                value: "{id}"
    """
    bundling = """
        resources:
          common:
            ignore_references_to: [Patient]
    """

    bundles, _ = run_bundler(tmp_path, rules=rules, bundling=bundling)

    for bundle in bundles:
        for entry in bundle["entry"]:
            assert "id" in entry["resource"]


# README "Important behaviour": a resource referenced by several results is added once per Bundle
def test_duplicates_are_added_once_per_bundle(tmp_path):
    # size 0: all results of the search page in one Bundle; all three Consents reference the same ResearchStudy
    bundling = """
        bundle:
          size: 0
        resources:
          common:
            ignore_references_to: [Patient]
    """

    bundles, _ = run_bundler(tmp_path, rules=GICS_RULES, bundling=bundling)

    assert len(bundles) == 1
    assert len(entries(bundles, "Consent")) == 3
    assert len(entries(bundles, "ResearchStudy")) == 1


# README "Important behaviour": empty Bundles are not written
def test_empty_bundles_are_not_written(tmp_path):
    rules = """
        Consent:
          filter:
            - skip:
                log: none
    """

    bundles, _ = run_bundler(tmp_path, rules=rules)

    assert bundles == []
    assert list(tmp_path.iterdir()) == []


# README "Exit codes and logging": each processed resource, fetched reference and written Bundle is logged (INFO)
def test_processing_is_logged(tmp_path, caplog):
    bundling = """
        resources:
          common:
            ignore_references_to: [Patient]
    """

    with caplog.at_level(logging.INFO):
        run_bundler(tmp_path, rules=GICS_RULES, bundling=bundling)

    assert "Processing resource Consent/4d835560-7b54-44b6-87ce-38a153ff3d9e" in caplog.messages
    assert "Fetching reference ResearchStudy/468b895a-c809-11f1-8d48-d6456abb6135..." in caplog.messages
    assert any(message.startswith("Writing bundle with 2 entries to file") for message in caplog.messages)
