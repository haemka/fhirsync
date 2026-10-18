import json

import pytest
from support import entries, read_bundles, write_and_load_config

from fhirsync import MissingReferenceError, ResourceBundler, SyncResult

# These tests run fhirsync with a source of type files: small FHIR files written to tmp_path/input, Bundles written to
# tmp_path/out (README settings reference: source of type files; bundling.resources on_missing_reference).

PATIENT = {"resourceType": "Patient", "id": "p1",
           "identifier": [{"system": "http://hospital.example/sid/patient", "value": "0012345"}]}
OBSERVATION = {"resourceType": "Observation", "id": "o1", "status": "final", "code": {"text": "Hb"},
               "identifier": [{"system": "http://hospital.example/sid/observation", "value": "o1"}],
               "subject": {"reference": "Patient/p1"}}


def files_config(tmp_path, source="", bundling=""):
    """A configuration with a files source reading tmp_path/input and Bundles written to tmp_path/out.

    source: more settings of the source section (YAML, indented by 2). bundling: the bundling section (YAML).
    """
    return write_and_load_config(tmp_path, f"""
source:
  type: files
  directory: {tmp_path}/input
{source}
target:
  mode: file
  outdir: {tmp_path}/out
bundling:
{bundling or "  bundle: {size: 1}"}
""")


def write(tmp_path, name, content):
    """Write a file below tmp_path/input: a resource as JSON, or text as it is."""
    path = tmp_path / "input" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content))


# Every resource in the files is a result; a reference is resolved from the files and rewritten to the identifier
def test_every_resource_is_a_result(tmp_path):
    write(tmp_path, "observation.json", OBSERVATION)
    write(tmp_path, "patient.json", PATIENT)

    result = ResourceBundler(files_config(tmp_path)).run()
    bundles = read_bundles(tmp_path / "out")

    # The Observation with its Patient, and the Patient on its own
    assert result == SyncResult(bundles=2, entries=3, rejected=0)
    assert len(entries(bundles, "Observation")) == 1
    assert len(entries(bundles, "Patient")) == 2
    assert entries(bundles, "Observation")[0]["resource"]["subject"] == {
        "reference": "Patient?identifier=http://hospital.example/sid/patient|0012345"}


# With resource_types, only these are results; the other resources are only used for references
def test_resource_types(tmp_path):
    write(tmp_path, "observation.json", OBSERVATION)
    write(tmp_path, "patient.json", PATIENT)

    result = ResourceBundler(files_config(tmp_path, source="  resource_types: [Observation]")).run()
    bundles = read_bundles(tmp_path / "out")

    assert result == SyncResult(bundles=1, entries=2, rejected=0)
    assert len(entries(bundles, "Observation")) == 1
    assert len(entries(bundles, "Patient")) == 1


# NDJSON files and Bundles in files are read; with bundle.size 0 every file is one Bundle
def test_ndjson_bundles_and_one_bundle_per_file(tmp_path):
    write(tmp_path, "Patient.ndjson", json.dumps(PATIENT) + "\n"
          + json.dumps({**PATIENT, "id": "p2", "identifier": [{"system": "http://hospital.example/sid/patient",
                                                               "value": "0067890"}]}) + "\n")
    write(tmp_path, "observations.json", {"resourceType": "Bundle", "type": "collection",
                                          "entry": [{"resource": OBSERVATION}]})

    result = ResourceBundler(files_config(tmp_path, bundling="  bundle: {size: 0}")).run()
    bundles = read_bundles(tmp_path / "out")

    # Patient.ndjson: p1 and p2; observations.json: the Observation and its Patient p1
    assert result == SyncResult(bundles=2, entries=4, rejected=0)
    assert sorted(len(bundle["entry"]) for bundle in bundles) == [2, 2]


# The settings of the readers are used: here only *.ndjson files, recursively
def test_patterns_and_recursive(tmp_path):
    write(tmp_path, "sub/Patient.ndjson", json.dumps(PATIENT) + "\n")
    write(tmp_path, "observation.json", OBSERVATION)

    result = ResourceBundler(files_config(tmp_path, source="  patterns: ['*.ndjson']\n  recursive: true")).run()

    assert result == SyncResult(bundles=1, entries=1, rejected=0)
    assert len(entries(read_bundles(tmp_path / "out"), "Patient")) == 1


# with on_error skip, an invalid file is skipped with a warning and the others are synced
def test_on_error_skip(tmp_path, caplog):
    write(tmp_path, "broken.json", '{"resourceType": "Patient",')
    write(tmp_path, "patient.json", PATIENT)

    result = ResourceBundler(files_config(tmp_path, source="  on_error: skip")).run()

    assert result == SyncResult(bundles=1, entries=1, rejected=0)
    assert any("broken.json: invalid JSON" in message for message in caplog.messages)


# on_missing_reference error (the default): a reference the files don't contain stops the run
def test_missing_reference_is_an_error(tmp_path):
    write(tmp_path, "observation.json", OBSERVATION)

    with pytest.raises(MissingReferenceError, match="Reference Patient/p1 of Observation/o1 not found in the source. "
                                                    "Set bundling.resources.Observation.on_missing_reference to "
                                                    "warning to continue without it."):
        ResourceBundler(files_config(tmp_path)).run()


# on_missing_reference warning: the missing reference is logged and left unchanged, the run continues
def test_missing_reference_warning(tmp_path, caplog):
    write(tmp_path, "observation.json", OBSERVATION)

    result = ResourceBundler(files_config(tmp_path, bundling="""
  resources:
    Observation:
      on_missing_reference: warning""")).run()

    assert result == SyncResult(bundles=1, entries=1, rejected=0)
    observation = entries(read_bundles(tmp_path / "out"), "Observation")[0]["resource"]
    assert observation["subject"] == {"reference": "Patient/p1"}
    assert ("Reference Patient/p1 of Observation/o1 not found in the source, the reference is left unchanged."
            in caplog.messages)


# References written as Type?<parameters> are not supported for files: they count as missing
def test_search_reference_is_missing(tmp_path):
    write(tmp_path, "observation.json",
          {**OBSERVATION, "subject": {"reference": "Patient?identifier=http://hospital.example/sid/patient|0012345"}})
    write(tmp_path, "patient.json", PATIENT)

    with pytest.raises(MissingReferenceError, match=r"Reference Patient\?identifier=http://hospital.example/sid/"
                                                    r"patient\|0012345 of Observation/o1 not found in the source"):
        ResourceBundler(files_config(tmp_path, source="  resource_types: [Observation]")).run()


# A resource read twice (same type and id): a warning, references use the first one
def test_duplicate_resource(tmp_path, caplog):
    write(tmp_path, "a.json", PATIENT)
    write(tmp_path, "b.json", {**PATIENT, "gender": "female"})
    write(tmp_path, "observation.json", OBSERVATION)

    ResourceBundler(files_config(tmp_path, source="  resource_types: [Observation]")).run()

    assert any(message.endswith("b.json: Patient/p1 was already read from another file or line, the first one is "
                                "used for references") for message in caplog.messages)
    assert "gender" not in entries(read_bundles(tmp_path / "out"), "Patient")[0]["resource"]


# The files are read when the bundler is created, a missing directory is an error there
def test_missing_directory(tmp_path):
    config = files_config(tmp_path)

    with pytest.raises(FileNotFoundError, match="Directory not found: .*input"):
        ResourceBundler(config)


# README "Upgrading from fhirsync 0.8 and 0.9": a files source has no source client
def test_no_source_client(tmp_path):
    write(tmp_path, "patient.json", PATIENT)

    assert ResourceBundler(files_config(tmp_path)).source_client is None


# Searches need a server source: run() with searches and fetch_resources() are errors for a files source
def test_no_searches_for_files(tmp_path):
    write(tmp_path, "patient.json", PATIENT)
    bundler = ResourceBundler(files_config(tmp_path))

    with pytest.raises(ValueError, match="Searches need a source of type server."):
        bundler.run(["Patient"])
    with pytest.raises(ValueError, match="Searches need a source of type server."):
        bundler.fetch_resources("Patient")
