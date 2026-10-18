"""fhirsync: search FHIR resources, bundle them with their references and write them to a FHIR server or to files.

The public API, for applications that run fhirsync from their own code (see README "Using fhirsync as a library"):

    config = load_config("config.yaml")
    result = ResourceBundler(config).fetch_resources("Consent", params={"patient.identifier": "system|value"})

The command line is in fhirsync.cli.
"""
from fhirsync.bundler import MissingReferenceError, ResourceBundler, SyncResult
from fhirsync.config import Config, ConfigError, ConfigModel, load_config, load_yaml
from fhirsync.rules import RuleError

__all__ = ["Config", "ConfigError", "ConfigModel", "MissingReferenceError", "ResourceBundler", "RuleError",
           "SyncResult", "load_config", "load_yaml"]
