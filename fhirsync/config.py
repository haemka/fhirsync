"""Configuration of fhirsync, loaded from a YAML file and validated at startup.

load_yaml() and ConfigModel are also used by applications built on fhirsync for their own configuration files, so
these get the same environment variables, error messages and warnings.
"""
import difflib
import logging
import os
import re
from typing import Any, Dict, List, Literal, Optional, Tuple, Type, TypeVar, Union, get_args, get_origin

import yaml
from fhir.resources.R4B import get_fhir_model_class
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from fhirsync.rules import RuleEngine, RuleSet

logger = logging.getLogger(__name__)

ENV_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?}")
# Key for settings that apply to all resource types, overridden per resource type
COMMON = "common"
# A search: '<Type>' or '<Type>?<parameters>'
SEARCH = re.compile(r"([A-Za-z]+)(\?.+)?")
# The settings of each source type; settings of the other type are errors
SERVER_SETTINGS = ("base_url", "auth", "ca_file", "rfc5280", "next_param", "request_params", "searches")
FILES_SETTINGS = ("directory", "patterns", "recursive", "include_hidden", "follow_symlinks", "unpack_bundles",
                  "on_error", "resource_types")


class ConfigError(Exception):
    """Raised for an unreadable or invalid configuration file."""


class ConfigModel(BaseModel):
    """Base of all configuration sections."""
    # Unknown keys are errors, so typos don't go unnoticed
    model_config = ConfigDict(extra="forbid")

    def explicit(self, *names: str) -> List[str]:
        """The given variables that are set in the configuration file (not just defaults)."""
        return [name for name in names if name in self.model_fields_set and getattr(self, name) is not None]

    def require_together(self, *names: str) -> None:
        """All or none of the given variables must be set."""
        given = [name for name in names if getattr(self, name) is not None]
        if given and len(given) != len(names):
            missing = [name for name in names if name not in given]
            raise ValueError(f"{', '.join(given)} also {'needs' if len(given) == 1 else 'need'} {', '.join(missing)}")

    def ignored_settings(self) -> List[Tuple[str, str]]:
        """Variables set in the configuration file that have no effect, as (relative path, reason)."""
        return []

    def warnings(self) -> List[str]:
        """Messages for all variables in this section and the sections below it that have no effect."""
        messages = []

        def walk(model: ConfigModel, path: str) -> None:
            """
            Collect the messages of a section and, recursively, of the sections below it.

            :param model: The section.
            :type model: ConfigModel
            :param path: The section's path in the configuration file, e.g. bundling.resources.Consent.
            :type path: str
            """
            messages.extend(f"{path}.{name}: ignored, {reason}" if path else f"{name}: ignored, {reason}"
                            for name, reason in model.ignored_settings())
            for name in type(model).model_fields:
                value = getattr(model, name)
                children = value.items() if isinstance(value, dict) else [(None, value)]
                for key, child in children:
                    if isinstance(child, ConfigModel):
                        walk(child, ".".join(p for p in (path, name, key) if p))
        walk(self, "")
        return messages


class Auth(ConfigModel):
    """Basic authentication (username/password) or OAuth 2.0 (auth_url, realm, client_id, client_secret)."""
    username: Optional[str] = None
    password: Optional[str] = None
    auth_url: Optional[str] = None
    realm: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None

    @model_validator(mode="after")
    def check_complete(self) -> "Auth":
        """
        Check that basic authentication and OAuth 2.0 are each complete or not given at all.

        :return: The validated section.
        :rtype: Auth
        :raises ValueError: For a username without password (or the other way round), or an incomplete OAuth 2.0.
        """
        self.require_together("username", "password")
        # OAuth 2.0 is only used if all four are set, otherwise the client silently falls back to basic auth
        self.require_together("auth_url", "realm", "client_id", "client_secret")
        return self


class Common(ConfigModel):
    ca_file: Optional[str] = None
    rfc5280: bool = True


def check_resource_type(resource_type: str) -> None:
    """
    Check that a name is a FHIR R4B resource type, so that typos are reported at startup.

    :param resource_type: The name, e.g. 'Consent'.
    :type resource_type: str
    :raises ValueError: If it is not a resource type (also for data types like 'HumanName').
    """
    try:
        model_class = get_fhir_model_class(resource_type)
    except ValueError:
        model_class = None
    if model_class is None or not model_class.has_resource_base():
        raise ValueError(f"unknown resource type '{resource_type}'")


class Source(ConfigModel):
    """Where the resources come from: a FHIR server (type server) or FHIR files (type files)."""
    type: Literal["server", "files"] = "server"
    # type server
    base_url: Optional[str] = None
    auth: Optional[Auth] = None
    ca_file: Optional[str] = None
    rfc5280: Optional[bool] = None
    next_param: Optional[str] = None
    # Search parameters per resource type, 'common' for all. A list value repeats the parameter.
    request_params: Dict[str, Optional[Dict[str, Union[str, List[str]]]]] = {}
    # The searches of a run, '<Type>' or '<Type>?<parameters>'; -q replaces them
    searches: List[str] = []
    # type files: the files are listed with dic-helper-toolkit's DirectoryReader ...
    directory: Optional[str] = None
    patterns: List[str] = ["*.json", "*.ndjson", "*.xml", "*.json.gz", "*.ndjson.gz", "*.xml.gz"]
    recursive: bool = False
    include_hidden: bool = False
    follow_symlinks: bool = False
    # ... and read with its FHIRFileReader
    unpack_bundles: bool = True
    on_error: Literal["raise", "skip"] = "raise"
    # The resource types that are results, the others only serve as reference targets; empty for all
    resource_types: List[str] = []

    @model_validator(mode="after")
    def check_type(self) -> "Source":
        """
        Check the settings against the source type: type server needs base_url, type files needs directory, and
        the settings of the other type are not allowed. Resource types in searches and resource_types must exist.

        :return: The validated section.
        :rtype: Source
        :raises ValueError: For a missing base_url or directory, settings of the other type, an invalid search or
            an unknown resource type.
        """
        if self.type == "server" and not self.base_url:
            raise ValueError("type server needs base_url")
        if self.type == "files" and not self.directory:
            raise ValueError("type files needs directory")
        other = self.explicit(*(FILES_SETTINGS if self.type == "server" else SERVER_SETTINGS))
        if other:
            raise ValueError(f"{', '.join(other)} not allowed with type {self.type}")
        for search in self.searches:
            match = SEARCH.fullmatch(search)
            if not match:
                raise ValueError(f"invalid search '{search}', expected <Type> or <Type>?<parameters>")
            check_resource_type(match.group(1))
        for resource_type in self.resource_types:
            check_resource_type(resource_type)
        return self

    @field_validator("request_params")
    @classmethod
    def empty_as_none(cls, value):
        """
        Accept a resource type without parameters: a section with all entries commented out is null in YAML.

        :param value: The search parameters per resource type, as read.
        :type value: Dict[str, Optional[Dict[str, Union[str, List[str]]]]]
        :return: The search parameters, with an empty mapping instead of null.
        :rtype: Dict[str, Dict[str, Union[str, List[str]]]]
        """
        return {resource_type: params or {} for resource_type, params in value.items()}

    def request_params_for(self, resource_type: str) -> Dict[str, Union[str, List[str]]]:
        """Search parameters for a resource type: the common ones, overridden per parameter by the type's own."""
        return {**self.request_params.get(COMMON, {}), **self.request_params.get(resource_type, {})}


class Target(ConfigModel):
    mode: Literal["server", "file"]
    base_url: Optional[str] = None
    outdir: Optional[str] = None
    auth: Optional[Auth] = None
    ca_file: Optional[str] = None
    rfc5280: Optional[bool] = None

    @model_validator(mode="after")
    def check_mode(self) -> "Target":
        """
        Check that the mode has what it needs: base_url for server, outdir for file.

        :return: The validated section.
        :rtype: Target
        :raises ValueError: If base_url or outdir is missing for the mode.
        """
        if self.mode == "server" and not self.base_url:
            raise ValueError("mode server needs base_url")
        if self.mode == "file" and not self.outdir:
            raise ValueError("mode file needs outdir")
        return self

    def ignored_settings(self) -> List[Tuple[str, str]]:
        """
        The settings of the other mode: outdir with server; base_url, auth, ca_file and rfc5280 with file.

        :return: The ignored settings as (name, reason).
        :rtype: List[Tuple[str, str]]
        """
        unused = ("outdir",) if self.mode == "server" else ("base_url", "auth", "ca_file", "rfc5280")
        return [(name, f"not used in mode {self.mode}") for name in self.explicit(*unused)]


class Request(ConfigModel):
    """Request of a Bundle entry. Unset values are inherited from 'common', then from the defaults."""
    method: Optional[Literal["put", "post"]] = None
    url: Optional[Literal["identifier", "id"]] = None
    condition: Optional[Literal["ifNoneExist"]] = None


DEFAULT_REQUEST = Request(method="put", url="identifier")


class ResourceBundling(ConfigModel):
    """Settings for resources of one type (or 'common') when they are processed into a Bundle."""
    request: Optional[Request] = None
    # How references from these resources are written: unchanged or as conditional identifier references
    reference_mode: Optional[Literal["as_is", "identifier"]] = None
    # Resource types whose references from these resources are not followed (replaces the 'common' list)
    ignore_references_to: Optional[List[str]] = None
    # A reference from these resources whose target can't be found: stop the run, or log a warning and continue
    on_missing_reference: Optional[Literal["error", "warning"]] = None
    # The system of the identifier that counts for requests and rewritten references; without it the first one counts
    identifier_system: Optional[str] = None
    # No identifier with identifier_system: stop the run, or log a warning and use the first identifier
    on_missing_identifier_system: Optional[Literal["error", "first"]] = None


class BundleSettings(ConfigModel):
    """The output Bundles themselves."""
    type: Literal["transaction", "batch", "collection"] = "transaction"
    size: int = Field(1, ge=0, description="Search results per Bundle, 0 for all results of a search page")
    # System of Bundle.identifier, whose value is a random UUID (also the file name in target mode file)
    identifier_system: str = "http://www.uniklinikum-jena.de/fhir/sid/consentBundle"


class Bundling(ConfigModel):
    bundle: BundleSettings = BundleSettings()
    # Settings per resource type, 'common' for all
    resources: Dict[str, ResourceBundling] = {}

    def _setting_for(self, resource_type: str, name: str, default: Any) -> Any:
        """A single setting: the resource type's own, otherwise the common one, otherwise the default."""
        for key in (resource_type, COMMON):
            settings = self.resources.get(key)
            if settings and getattr(settings, name) is not None:
                return getattr(settings, name)
        return default

    def reference_mode_for(self, resource_type: str) -> str:
        """
        The effective reference_mode of a resource type: its own, otherwise the common one, otherwise identifier.

        :param resource_type: The resource type.
        :type resource_type: str
        :return: 'identifier' or 'as_is'.
        :rtype: str
        """
        return self._setting_for(resource_type, "reference_mode", "identifier")

    def on_missing_reference_for(self, resource_type: str) -> str:
        """
        The effective on_missing_reference of a resource type: its own, otherwise the common one, otherwise error.

        :param resource_type: The resource type.
        :type resource_type: str
        :return: 'error' or 'warning'.
        :rtype: str
        """
        return self._setting_for(resource_type, "on_missing_reference", "error")

    def identifier_system_for(self, resource_type: str) -> Optional[str]:
        """
        The effective identifier_system of a resource type: its own, otherwise the common one, otherwise none.

        :param resource_type: The resource type.
        :type resource_type: str
        :return: The identifier system, None if the first identifier counts.
        :rtype: Optional[str]
        """
        return self._setting_for(resource_type, "identifier_system", None)

    def on_missing_identifier_system_for(self, resource_type: str) -> str:
        """
        The effective on_missing_identifier_system of a resource type: its own, otherwise the common one, otherwise
        error.

        :param resource_type: The resource type.
        :type resource_type: str
        :return: 'error' or 'first'.
        :rtype: str
        """
        return self._setting_for(resource_type, "on_missing_identifier_system", "error")

    def ignore_references_to_for(self, resource_type: str) -> List[str]:
        """
        The effective ignore_references_to of a resource type: its own list replaces the common one, default none.

        :param resource_type: The resource type.
        :type resource_type: str
        :return: The resource types whose references are not followed.
        :rtype: List[str]
        """
        return list(self._setting_for(resource_type, "ignore_references_to", []))

    def request_for(self, resource_type: str) -> Request:
        """The effective request: defaults, overridden by 'common', overridden by the resource type."""
        values = DEFAULT_REQUEST.model_dump()
        for key in (COMMON, resource_type):
            settings = self.resources.get(key)
            if settings and settings.request:
                values.update(settings.request.model_dump(exclude_unset=True))
        return Request(**values)

    def ignored_settings(self) -> List[Tuple[str, str]]:
        """
        The request settings that don't fit the effective method (url with post, condition with put), and
        on_missing_identifier_system without an effective identifier_system.

        :return: The ignored settings as (path below bundling, reason).
        :rtype: List[Tuple[str, str]]
        """
        ignored = []
        for key, settings in self.resources.items():
            if settings.on_missing_identifier_system is not None and self.identifier_system_for(key) is None:
                ignored.append((f"resources.{key}.on_missing_identifier_system", "not used without identifier_system"))
            if not settings.request:
                continue
            method = self.request_for(key).method
            if method == "post":
                # A create always targets the resource type
                ignored += [(f"resources.{key}.request.url", "not used with method post")
                            for _ in settings.request.explicit("url")]
            if method == "put":
                ignored += [(f"resources.{key}.request.condition", "not used with method put")
                            for _ in settings.request.explicit("condition")]
        return ignored


class Processing(ConfigModel):
    # Identifier systems per resource type, available in rules as identifierSystem('<Type>')
    identifier_systems: Dict[str, str] = {}
    # Rules per resource type ('*' for all) and phase, see fhirsync.rules
    rules: Dict[str, RuleSet] = {}

    @model_validator(mode="after")
    def check_rules(self) -> "Processing":
        """
        Check all rule expressions at startup, with RuleEngine.check().

        :return: The validated section.
        :rtype: Processing
        :raises ValueError: With all invalid expressions, one per line.
        """
        problems = self.engine().check()
        if problems:
            raise ValueError("invalid rule expressions:\n    " + "\n    ".join(problems))
        return self

    def engine(self) -> RuleEngine:
        """
        The rule engine for these rules and identifier systems.

        :return: A new rule engine.
        :rtype: RuleEngine
        """
        return RuleEngine(self.rules, self.identifier_systems)


class Config(ConfigModel):
    common: Common = Common()
    source: Source
    target: Target
    bundling: Bundling = Bundling()
    processing: Processing = Processing()

    def tls(self, section: Union[Source, Target]) -> Tuple[Optional[str], bool]:
        """CA file and RFC 5280 setting of a section, falling back to the common settings."""
        ca_file = section.ca_file if section.ca_file is not None else self.common.ca_file
        rfc5280 = section.rfc5280 if section.rfc5280 is not None else self.common.rfc5280
        return ca_file, rfc5280


def substitute_env(value: Any, path: str = "") -> Any:
    """Replace ${NAME} and ${NAME:-default} in all string values with environment variables."""
    if isinstance(value, dict):
        return {k: substitute_env(v, f"{path}.{k}" if path else str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute_env(v, f"{path}[{i}]") for i, v in enumerate(value)]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match) -> str:
        """
        The replacement for one ${NAME} or ${NAME:-default}.

        :param match: The match of ENV_VARIABLE.
        :type match: re.Match
        :return: The variable's value, otherwise the default.
        :rtype: str
        :raises ConfigError: If the variable is not set and has no default.
        """
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        raise ConfigError(f"{path}: environment variable {name} is not set")
    return ENV_VARIABLE.sub(replace, value)


def _model_at(model: Type[BaseModel], location: Tuple) -> Optional[type]:
    """The configuration model at an error location, e.g. Request for ('bundling', 'resources', 'X', 'request')."""
    current: Any = model
    parts = list(location)
    while parts:
        if isinstance(current, type) and issubclass(current, BaseModel):
            field = current.model_fields.get(parts.pop(0))
            if field is None:
                return None
            current = field.annotation
        elif get_origin(current) is Union:
            current = next((a for a in get_args(current) if a is not type(None)), None)
        elif get_origin(current) is dict:
            current = get_args(current)[1]
            parts.pop(0)
        elif get_origin(current) is list:
            current = get_args(current)[0]
            parts.pop(0)
        else:
            return None
    while get_origin(current) is Union:
        current = next((a for a in get_args(current) if a is not type(None)), None)
    return current if isinstance(current, type) and issubclass(current, BaseModel) else None


def _message(model: Type[BaseModel], error: Dict[str, Any]) -> str:
    """A readable message for a validation error."""
    if error["type"] == "extra_forbidden":
        section = _model_at(model, tuple(error["loc"][:-1]))
        known = list(section.model_fields) if section else []
        close = difflib.get_close_matches(str(error["loc"][-1]), known, n=1)
        return "unknown configuration variable" + (f", did you mean '{close[0]}'?" if close else "")
    if error["type"] == "missing":
        return "required configuration variable is missing"
    return error["msg"].removeprefix("Value error, ")


M = TypeVar("M", bound=ConfigModel)


def load_yaml(path: str, model: Type[M]) -> M:
    """Load, substitute and validate a YAML configuration file with the given model.

    Raises ConfigError with all problems at once. Variables without effect are logged as warnings.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError as e:
        raise ConfigError(f"Cannot read configuration file {path}: {e.strerror}") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    try:
        config = model.model_validate(substitute_env(data))
    except ValidationError as e:
        problems = [f"  {'.'.join(str(p) for p in error['loc']) or '(root)'}: {_message(model, error)}"
                    for error in e.errors()]
        raise ConfigError(f"Invalid configuration in {path}:\n" + "\n".join(problems)) from None
    for message in config.warnings():
        logger.warning(f"Configuration {path}: {message}")
    return config


def load_config(path: str) -> Config:
    """Load a fhirsync configuration file, see load_yaml()."""
    return load_yaml(path, Config)
