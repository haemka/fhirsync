import pytest
from support import write_and_load_config

from fhirsync.config import ConfigError

# These tests check the source types server and files, the searches of a server source and on_missing_reference
# (README settings reference: source, bundling).


# README settings reference: type defaults to server, so configurations without it stay valid; searches default to none
def test_type_defaults_to_server(tmp_path):
    config = write_and_load_config(tmp_path, """
        source:
          base_url: https://gics.example/fhir
        target:
          mode: file
          outdir: ./out
    """)

    assert config.source.type == "server"
    assert config.source.searches == []


# README settings reference: source.searches lists the searches of a run
def test_searches(tmp_path):
    config = write_and_load_config(tmp_path, """
        source:
          type: server
          base_url: https://gics.example/fhir
          searches:
            - Consent?domain:identifier=MII
            - ResearchStudy
        target:
          mode: file
          outdir: ./out
    """)

    assert config.source.searches == ["Consent?domain:identifier=MII", "ResearchStudy"]


# README settings reference: a search is <Type> or <Type>?<parameters>
def test_invalid_search(tmp_path):
    with pytest.raises(ConfigError, match=r"source: invalid search '/Consent', expected <Type> or "
                                          r"<Type>\?<parameters>"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
              searches: [/Consent]
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: the resource type of a search must exist
def test_search_with_unknown_resource_type(tmp_path):
    with pytest.raises(ConfigError, match="source: unknown resource type 'Consnet'"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
              searches: ["Consnet?domain:identifier=MII"]
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: type server needs base_url
def test_server_without_base_url(tmp_path):
    with pytest.raises(ConfigError, match="source: type server needs base_url"):
        write_and_load_config(tmp_path, """
            source:
              type: server
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: type files needs directory, the other settings have defaults
def test_files_source_defaults(tmp_path):
    config = write_and_load_config(tmp_path, """
        source:
          type: files
          directory: /data/export
        target:
          mode: file
          outdir: ./out
    """)

    assert config.source.directory == "/data/export"
    assert config.source.patterns == ["*.json", "*.ndjson", "*.xml", "*.json.gz", "*.ndjson.gz", "*.xml.gz"]
    assert config.source.recursive is False
    assert config.source.include_hidden is False
    assert config.source.follow_symlinks is False
    assert config.source.unpack_bundles is True
    assert config.source.on_error == "raise"
    assert config.source.resource_types == []


# README settings reference: all settings of a files source
def test_files_source(tmp_path, caplog):
    config = write_and_load_config(tmp_path, """
        source:
          type: files
          directory: /data/export
          patterns: ["*.ndjson"]
          recursive: true
          include_hidden: true
          follow_symlinks: true
          unpack_bundles: false
          on_error: skip
          resource_types: [Consent]
        target:
          mode: file
          outdir: ./out
    """)

    assert caplog.records == []  # no warnings
    assert config.source.patterns == ["*.ndjson"]
    assert config.source.recursive is True
    assert config.source.include_hidden is True
    assert config.source.follow_symlinks is True
    assert config.source.unpack_bundles is False
    assert config.source.on_error == "skip"
    assert config.source.resource_types == ["Consent"]


# README settings reference: type files needs directory
def test_files_without_directory(tmp_path):
    with pytest.raises(ConfigError, match="source: type files needs directory"):
        write_and_load_config(tmp_path, """
            source:
              type: files
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: settings of type server are errors with type files ...
def test_server_settings_with_files(tmp_path):
    with pytest.raises(ConfigError, match="source: base_url, next_param, searches not allowed with type files"):
        write_and_load_config(tmp_path, """
            source:
              type: files
              directory: /data/export
              base_url: https://gics.example/fhir
              next_param: _offset
              searches: [Consent]
            target:
              mode: file
              outdir: ./out
        """)


# ... and settings of type files with type server
def test_files_settings_with_server(tmp_path):
    with pytest.raises(ConfigError, match="source: directory, recursive not allowed with type server"):
        write_and_load_config(tmp_path, """
            source:
              base_url: https://gics.example/fhir
              directory: /data/export
              recursive: true
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: on_error is raise or skip
def test_invalid_on_error(tmp_path):
    with pytest.raises(ConfigError, match="source.on_error: Input should be 'raise' or 'skip'"):
        write_and_load_config(tmp_path, """
            source:
              type: files
              directory: /data/export
              on_error: ignore
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: resource_types must be resource types (a data type like HumanName isn't one)
def test_unknown_resource_type_in_resource_types(tmp_path):
    with pytest.raises(ConfigError, match="source: unknown resource type 'HumanName'"):
        write_and_load_config(tmp_path, """
            source:
              type: files
              directory: /data/export
              resource_types: [Consent, HumanName]
            target:
              mode: file
              outdir: ./out
        """)


# README settings reference: on_missing_reference defaults to error, common applies to all types, a type's own
# setting overrides it
def test_on_missing_reference(tmp_path):
    default = write_and_load_config(tmp_path, """
        source: {base_url: https://gics.example/fhir}
        target: {mode: file, outdir: ./out}
    """)
    configured = write_and_load_config(tmp_path, """
        source: {base_url: https://gics.example/fhir}
        target: {mode: file, outdir: ./out}
        bundling:
          resources:
            common:
              on_missing_reference: warning
            Consent:
              on_missing_reference: error
    """)

    assert default.bundling.on_missing_reference_for("Consent") == "error"
    assert configured.bundling.on_missing_reference_for("Consent") == "error"
    assert configured.bundling.on_missing_reference_for("ResearchStudy") == "warning"


# README settings reference: on_missing_reference is error or warning
def test_invalid_on_missing_reference(tmp_path):
    with pytest.raises(ConfigError, match="bundling.resources.common.on_missing_reference: Input should be 'error' "
                                          "or 'warning'"):
        write_and_load_config(tmp_path, """
            source: {base_url: https://gics.example/fhir}
            target: {mode: file, outdir: ./out}
            bundling:
              resources:
                common:
                  on_missing_reference: ignore
        """)
