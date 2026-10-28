"""Functions used by many tests: apply rules, run the bundler on recorded gICS data, load a configuration."""
import glob
import json
import os
import textwrap

import responses
import yaml
from fhir.resources.R4B import get_fhir_model_class
from responses import matchers

from fhirsync.bundler import ResourceBundler
from fhirsync.config import Config, Processing, load_config

DATA = os.path.join(os.path.dirname(__file__), "data")

# Recorded responses of gICS 2026.1.1 (template MII 1.6.d): the consent-status search of the MII domain with three
# valid patients and one with the invalid pseudonym 'studypsn42', plus their ResearchStudy and Patients.
# Recorded from a local gICS 2026.1.1 instance.
RECORDING = os.path.join(DATA, "gics_consent_status_search.json")

# The identifier systems the examples in docs/rules.md use
IDENTIFIER_SYSTEMS = {
    "Patient": "http://hospital.example/sid/patient",
    "Consent": "http://hospital.example/sid/consent",
    "ResearchStudy": "http://hospital.example/sid/consentSource",
    "Observation": "http://hospital.example/sid/observation",
}

with open(os.path.join(DATA, "gics_rules.yaml"), encoding="utf-8") as rules_file:
    GICS_RULES = rules_file.read()

with open(RECORDING, encoding="utf-8") as recording_file:
    SOURCE_BASE_URL = json.load(recording_file)["base_url"]


def apply_rules(rules, resource):
    """Apply the rules (YAML, the content of processing.rules) to a resource, like the bundler does.

    Returns the resource after the filter, transform and finalize phases, or None if a rule skipped it.
    """
    engine = Processing.model_validate({
        "identifier_systems": IDENTIFIER_SYSTEMS,
        "rules": yaml.safe_load(textwrap.dedent(rules)) or {},
    }).engine()
    model = get_fhir_model_class(resource["resourceType"]).model_validate(resource)

    variables = {}
    model = engine.apply("filter", model, variables)
    if model is None:
        return None
    model = engine.apply("transform", model, variables)
    model = engine.apply("finalize", model, {})
    return json.loads(model.model_dump_json())


# The identifier systems run_bundler() configures
BUNDLER_IDENTIFIER_SYSTEMS = {
    "Patient": "http://www.example-hospital.de/fhir/sid/patient",
    "Consent": "http://www.example-hospital.de/fhir/sid/consent",
    "ResearchStudy": "http://www.example-hospital.de/fhir/sid/researchstudy",
}


def run_bundler(outdir, rules="", bundling="", query=None, extra_responses=()):
    """Run the bundler on the recorded gICS responses and return the written Bundles and the requested URLs.

    rules: the content of processing.rules (YAML), with BUNDLER_IDENTIFIER_SYSTEMS as identifier systems.
    bundling: the content of the bundling section (YAML).
    query: the search to run, by default the recorded consent-status search.
    extra_responses: more source responses as (url, body).
    """
    with open(RECORDING, encoding="utf-8") as f:
        recording = json.load(f)
    config = Config.model_validate({
        "source": {"base_url": recording["base_url"]},
        "target": {"mode": "file", "outdir": str(outdir)},
        "bundling": yaml.safe_load(textwrap.dedent(bundling)) or {},
        "processing": {
            "identifier_systems": BUNDLER_IDENTIFIER_SYSTEMS,
            "rules": yaml.safe_load(textwrap.dedent(rules)) or {},
        },
    })

    source_responses = [(r["url"], r["body"]) for r in recording["responses"]] + list(extra_responses)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as source:
        for url, body in source_responses:
            query_string = url.split("?", 1)[1] if "?" in url else ""
            source.add("GET", url, json=body, match=[matchers.query_string_matcher(query_string)])
        ResourceBundler(config).fetch_resources(query or recording["query"])
        requested_urls = [call.request.url for call in source.calls]

    bundles = []
    for name in sorted(os.listdir(outdir)):
        with open(os.path.join(outdir, name), encoding="utf-8") as f:
            bundles.append(json.load(f))
    return bundles, requested_urls


def entries(bundles, resource_type):
    """The entries of one resource type in all Bundles."""
    return [entry for bundle in bundles for entry in bundle["entry"]
            if entry["resource"]["resourceType"] == resource_type]


def write_and_load_config(tmp_path, config):
    """Write a configuration (YAML) to a file and load it like fhirsync does."""
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(config), encoding="utf-8")
    return load_config(str(path))


def read_bundles(directory):
    """Read all Bundles of a directory, sorted, with the random Bundle identifier replaced by '<random>'.

    Used to compare written Bundles with recorded ones.
    """
    bundles = []
    for path in glob.glob(os.path.join(directory, "*.json")):
        with open(path, encoding="utf-8") as file:
            bundle = json.load(file)
        bundle["identifier"]["value"] = "<random>"
        bundles.append(bundle)
    # fhirsync writes the files in no particular order
    return sorted(bundles, key=lambda bundle: json.dumps(bundle, sort_keys=True))
