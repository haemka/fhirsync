import pytest
from support import GICS_RULES, entries, run_bundler

# These tests run the bundler on the recorded gICS consent-status Consents (see test_pipeline.py).

# Only removes the source ids (docs/rules.md 7.1). gICS Consents have no identifier, and these rules don't add one.
FINALIZE_ONLY = """
    "*":
      finalize:
        - delete: [id, meta.lastUpdated]
"""


# docs/rules.md 6: with request.url identifier (the default), a resource without identifier stops the whole run
def test_resource_without_identifier_stops(tmp_path):
    bundling = """
        resources:
          common:
            ignore_references_to: [Patient]
    """

    with pytest.raises(ValueError, match=r"^Resource Consent/[0-9a-f-]+ has no identifier\.$"):
        run_bundler(tmp_path, rules=FINALIZE_ONLY, bundling=bundling)


# docs/rules.md 6: otherwise use request.url id ...
def test_request_url_id_needs_no_identifier(tmp_path):
    bundling = """
        resources:
          common:
            request:
              url: id
            ignore_references_to: [Patient]
    """

    bundles, _ = run_bundler(tmp_path, rules=FINALIZE_ONLY, bundling=bundling)

    for entry in entries(bundles, "Consent"):
        assert entry["request"]["method"] == "PUT"
        assert entry["request"]["url"].startswith("Consent/")


# docs/rules.md 6: ... or method post
def test_post_needs_no_identifier(tmp_path):
    bundling = """
        resources:
          common:
            request:
              method: post
            ignore_references_to: [Patient]
    """

    bundles, _ = run_bundler(tmp_path, rules=FINALIZE_ONLY, bundling=bundling)

    for entry in entries(bundles, "Consent"):
        assert entry["request"] == {"method": "POST", "url": "Consent"}


# README "Important behaviour": post with condition ifNoneExist also needs an identifier, without one the run stops
def test_if_none_exist_without_identifier_stops(tmp_path):
    bundling = """
        resources:
          common:
            request:
              method: post
              condition: ifNoneExist
            ignore_references_to: [Patient]
    """

    with pytest.raises(ValueError, match=r"^Resource Consent/[0-9a-f-]+ has no identifier\.$"):
        run_bundler(tmp_path, rules=FINALIZE_ONLY, bundling=bundling)


# docs/rules.md 6: the request URL uses the first identifier
def test_request_url_uses_identifier(tmp_path):
    bundling = """
        resources:
          common:
            ignore_references_to: [Patient]
    """

    bundles, _ = run_bundler(tmp_path, rules=GICS_RULES, bundling=bundling)

    for entry in entries(bundles, "Consent"):
        identifier = entry["resource"]["identifier"][0]
        assert entry["request"] == {
            "method": "PUT",
            "url": f"Consent?identifier={identifier['system']}|{identifier['value']}",
        }


# docs/rules.md 6: ifNoneExist and rewritten references use the first identifier
def test_first_identifier_counts(tmp_path):
    # The ResearchStudy keeps gICS's identifier as second one and gets its own first (docs/rules.md 7.6).
    # It is written with post ifNoneExist.
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
            - set:
                path: identifier[1].system
                value: "{identifier[0].system}"
            - set:
                path: identifier[1].value
                value: "{identifier[0].value}"
            - set:
                path: identifier[0].system
                value: "{identifierSystem('ResearchStudy')}"
            - set:
                path: identifier[0].value
                value: "{id}"
    """
    bundling = """
        resources:
          common:
            ignore_references_to: [Patient]
          ResearchStudy:
            request:
              method: post
              condition: ifNoneExist
    """

    bundles, _ = run_bundler(tmp_path, rules=rules, bundling=bundling)

    study = entries(bundles, "ResearchStudy")[0]
    first_identifier = "http://www.example-hospital.de/fhir/sid/researchstudy|468b895a-c809-11f1-8d48-d6456abb6135"
    assert study["resource"]["identifier"][1] == {"system": "https://ths-greifswald.de/fhir/gics/", "value": "MII"}
    assert study["request"] == {"method": "POST", "url": "ResearchStudy", "ifNoneExist": f"identifier={first_identifier}"}
    for entry in entries(bundles, "Consent"):
        # extension DomainReference > extension domain > valueReference
        reference = entry["resource"]["extension"][0]["extension"][0]["valueReference"]["reference"]
        assert reference == f"ResearchStudy?identifier={first_identifier}"
