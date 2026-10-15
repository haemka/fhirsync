import logging

import pytest
from support import GICS_RULES, SOURCE_BASE_URL, entries, run_bundler

# The recorded gICS Consents reference their ResearchStudy in an extension and their gICS Patient in patient.reference.

RESEARCH_STUDY_ID = "468b895a-c809-11f1-8d48-d6456abb6135"


def research_study_reference(consent):
    # extension DomainReference > extension domain > valueReference
    return consent["extension"][0]["extension"][0]["valueReference"]["reference"]


# docs/rules.md 6: references to resources in the Bundle are rewritten to their identifier
def test_references_are_rewritten(tmp_path):
    bundling = """
        resources:
          common:
            ignore_references_to: [Patient]
    """

    bundles, _ = run_bundler(tmp_path, rules=GICS_RULES, bundling=bundling)

    for entry in entries(bundles, "Consent"):
        assert research_study_reference(entry["resource"]) == \
            f"ResearchStudy?identifier=http://www.example-hospital.de/fhir/sid/researchstudy|{RESEARCH_STUDY_ID}"


# docs/rules.md 1: transform rules already see the rewritten references
def test_transform_sees_rewritten_references(tmp_path):
    # identifiers for Consent and ResearchStudy, and the ResearchStudy reference of the Consent copied to meta.source
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
            - set:
                path: meta.source
                value: "{extension('http://fhir.de/ConsentManagement/StructureDefinition/DomainReference').extension('domain').value.reference}"
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

    for entry in entries(bundles, "Consent"):
        assert entry["resource"]["meta"]["source"] == \
            f"ResearchStudy?identifier=http://www.example-hospital.de/fhir/sid/researchstudy|{RESEARCH_STUDY_ID}"


# README settings reference: reference_mode as_is leaves the references of a type unchanged; the referenced resources
# are still fetched and added to the Bundle
def test_reference_mode_as_is(tmp_path):
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
          Consent:
            reference_mode: as_is
    """

    bundles, _ = run_bundler(tmp_path, rules=rules, bundling=bundling)

    assert len(entries(bundles, "ResearchStudy")) == 3
    for entry in entries(bundles, "Consent"):
        assert research_study_reference(entry["resource"]) == f"ResearchStudy/{RESEARCH_STUDY_ID}"


# docs/rules.md 3.1: if a referenced resource is skipped, the reference to it is not rewritten
def test_skipped_referenced_resource(tmp_path):
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
          filter:
            - skip:
                log: none
    """
    bundling = """
        resources:
          common:
            ignore_references_to: [Patient]
    """

    bundles, _ = run_bundler(tmp_path, rules=rules, bundling=bundling)

    assert entries(bundles, "ResearchStudy") == []
    for entry in entries(bundles, "Consent"):
        assert research_study_reference(entry["resource"]) == f"ResearchStudy/{RESEARCH_STUDY_ID}"


# docs/rules.md 6: references to ignored types are not followed and not rewritten
def test_ignored_references(tmp_path):
    # the gICS Consent rules without the rule that sets patient.reference
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

    bundles, requested_urls = run_bundler(tmp_path, rules=rules, bundling=bundling)

    assert not any("/Patient/" in url for url in requested_urls)
    assert entries(bundles, "Patient") == []
    for entry in entries(bundles, "Consent"):
        assert entry["resource"]["patient"]["reference"].startswith("Patient/")


# docs/rules.md 8: absolute URLs and #contained references are not followed, an error is logged
def test_references_that_are_not_type_and_id(tmp_path, caplog, gics_consent):
    gics_consent["patient"]["reference"] = "https://other.example/fhir/Patient/1"
    gics_consent["extension"] = [{"url": "http://example.org/ext", "valueReference": {"reference": "#contained-1"}}]
    search_result = {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": gics_consent}]}
    bundling = """
        resources:
          common:
            request:
              url: id
    """

    bundles, requested_urls = run_bundler(tmp_path, bundling=bundling, query="Consent?x=refs", extra_responses=[
        (f"{SOURCE_BASE_URL}/Consent?x=refs&_count=50", search_result),
    ])

    # only the search, the references are not followed
    assert len(requested_urls) == 1
    assert len(entries(bundles, "Consent")) == 1
    errors = [record.getMessage() for record in caplog.records if record.levelno == logging.ERROR]
    assert any("Malformed reference 'https://other.example/fhir/Patient/1'" in message for message in errors)
    assert any("Malformed reference '#contained-1'" in message for message in errors)


# docs/rules.md 8, known limitation: a search URL reference at the source should bring the found resource into the
# Bundle, but fhirsync processes the search result page instead. Remove the xfail mark when this is fixed.
@pytest.mark.xfail(strict=True, reason="known limitation, docs/rules.md section 8")
def test_search_url_reference_at_the_source(tmp_path, gics_consent, patient):
    gics_consent["patient"]["reference"] = "Patient?identifier=http://hospital.example/sid/mrn|0012345"
    del gics_consent["extension"]
    consents = {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": gics_consent}]}
    patients = {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": patient}]}
    bundling = """
        resources:
          common:
            request:
              url: id
    """

    bundles, _ = run_bundler(tmp_path, bundling=bundling, query="Consent?x=search", extra_responses=[
        (f"{SOURCE_BASE_URL}/Consent?x=search&_count=50", consents),
        (f"{SOURCE_BASE_URL}/Patient?identifier=http%3A%2F%2Fhospital.example%2Fsid%2Fmrn%7C0012345&_count=50",
         patients),
    ])

    assert len(entries(bundles, "Patient")) == 1
