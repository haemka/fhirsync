"""Declarative processing rules for FHIR resources.

Rules are configured per resource type ('*' for all types) and phase:
  filter    before references are resolved: skip resources, extract values into variables
  transform after references are resolved: change the resource
  finalize  after the Bundle entry request is built: clean up the resource for output
The '*' rules of a phase run before the rules of the resource type.

Values in rules are FHIRPath expressions (in 'when' and 'from') or templates with FHIRPath expressions in braces
('Patient?identifier={identifierSystem('Patient')}|{%pat_id}'). Variables extracted in the filter phase are available as
%name in the transform phase. Additional functions: identifierSystem('<Type>') returns the configured identifier system,
sha256() hashes its input.
"""
import hashlib
import importlib
import json
import logging
import re
from typing import Any, Callable, Dict, List, Literal, Optional, Union

from fhir.resources.R4B import get_fhir_model_class
from fhir.resources.R4B.resource import Resource
from fhirpathpy import evaluate
from fhirpathpy.models import models
from pydantic import BaseModel, ConfigDict, model_validator

logger = logging.getLogger(__name__)

PHASES = ("filter", "transform", "finalize")
# Operations allowed per phase
PHASE_OPERATIONS = {
    "filter": ("skip", "extract", "python"),
    "transform": ("set", "delete", "set_identifier", "python"),
    "finalize": ("set", "delete", "set_identifier", "python"),
}
ELEMENT_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[\d+\])?(\.[A-Za-z_][A-Za-z0-9_]*(\[\d+\])?)*$")
PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([^{}]+)\}")
# The FHIR model lets FHIRPath resolve choice elements like value[x] (e.g. value.ofType(Quantity))
FHIR_MODEL = models["r4"]


class RuleError(Exception):
    """Raised when a rule can't be applied to a resource."""


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Skip(Model):
    when: Optional[str] = None                  # FHIRPath condition, the resource is skipped if it is true
    log: Literal["warning", "info", "none"] = "warning"


class Extract(Model):
    name: str                                   # variable name, available as %name
    from_: str                                  # FHIRPath expression, its first result is used
    pattern: Optional[str] = None               # regular expression, its first group (or whole match) is used
    on_mismatch: Literal["skip", "error", "empty"] = "skip"
    model_config = ConfigDict(extra="forbid", populate_by_name=True,
                              alias_generator=lambda name: "from" if name == "from_" else name)

    @model_validator(mode="after")
    def check(self) -> "Extract":
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.name):
            raise ValueError(f"invalid variable name '{self.name}'")
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as e:
                raise ValueError(f"invalid pattern: {e}") from None
        return self


class Set(Model):
    path: str                                   # element path, e.g. meta.profile[0]
    value: Any                                  # template string, or a literal value

    @model_validator(mode="after")
    def check(self) -> "Set":
        check_path(self.path)
        return self


class SetIdentifier(Model):
    system: str                                 # template
    value: str                                  # template


class Python(Model):
    function: str                               # module:function, called with (resource dict, variables dict)

    @model_validator(mode="after")
    def check(self) -> "Python":
        if ":" not in self.function:
            raise ValueError("function must be given as module:function")
        return self


class Rule(Model):
    """A single rule, exactly one operation."""
    skip: Optional[Skip] = None
    extract: Optional[Extract] = None
    set: Optional[Set] = None
    delete: Optional[Union[str, List[str]]] = None
    set_identifier: Optional[SetIdentifier] = None
    python: Optional[Python] = None

    @model_validator(mode="before")
    @classmethod
    def skip_shorthand(cls, data: Any) -> Any:
        # 'skip: {}' and 'skip:' (null) both mean: skip unconditionally
        if isinstance(data, dict) and "skip" in data and data["skip"] is None:
            data = {**data, "skip": {}}
        return data

    @model_validator(mode="after")
    def check(self) -> "Rule":
        given = self.operations()
        if len(given) != 1:
            raise ValueError(f"a rule needs exactly one operation of {', '.join(Rule.model_fields)}, "
                             f"got {', '.join(given) or 'none'}")
        if self.delete is not None:
            for path in self.paths_to_delete():
                check_path(path)
        return self

    def operations(self) -> List[str]:
        return [name for name in Rule.model_fields if getattr(self, name) is not None]

    def operation(self) -> str:
        return self.operations()[0]

    def paths_to_delete(self) -> List[str]:
        return [self.delete] if isinstance(self.delete, str) else list(self.delete)


class RuleSet(Model):
    """Rules of one resource type, per phase."""
    filter: List[Rule] = []
    transform: List[Rule] = []
    finalize: List[Rule] = []

    @model_validator(mode="after")
    def check_phases(self) -> "RuleSet":
        for phase in PHASES:
            for index, rule in enumerate(getattr(self, phase)):
                if rule.operation() not in PHASE_OPERATIONS[phase]:
                    raise ValueError(f"{phase}[{index}]: {rule.operation()} is not allowed in phase {phase}, "
                                     f"allowed are {', '.join(PHASE_OPERATIONS[phase])}")
        return self


def check_path(path: str) -> None:
    if not ELEMENT_PATH.match(path):
        raise ValueError(f"invalid element path '{path}', expected e.g. patient.reference or meta.profile[0]")


def parse_path(path: str) -> List[Union[str, int]]:
    """Split an element path like meta.profile[0] into ['meta', 'profile', 0]."""
    steps: List[Union[str, int]] = []
    for part in path.split("."):
        name, _, index = part.partition("[")
        steps.append(name)
        if index:
            steps.append(int(index.rstrip("]")))
    return steps


class RuleEngine:
    """Applies the configured rules to resources.

    :param rules: Rule sets per resource type, '*' for all types.
    :param identifier_systems: Identifier systems per resource type, for identifierSystem('<Type>').
    """

    def __init__(self, rules: Dict[str, RuleSet], identifier_systems: Dict[str, str]) -> None:
        self.rules = rules
        self.identifier_systems = identifier_systems
        self.functions = {
            "identifierSystem": {"fn": self._identifier_system, "arity": {1: ["String"]}},
            "sha256": {"fn": lambda inputs: [hashlib.sha256(str(i).encode()).hexdigest() for i in inputs],
                       "arity": {0: []}},
        }

    def _identifier_system(self, inputs: List[Any], resource_type: str) -> List[str]:
        if resource_type not in self.identifier_systems:
            raise RuleError(f"no identifier system configured for {resource_type} in processing.identifier_systems")
        return [self.identifier_systems[resource_type]]

    # -- validation ----------------------------------------------------------------------------------------------

    def check(self) -> List[str]:
        """Check expressions and templates of all rules against a dummy resource. Returns error messages."""
        problems = []
        dummy = {"resourceType": "Basic"}
        for resource_type, rule_set in self.rules.items():
            variables = {}
            for rule in self.rules.get("*", RuleSet()).filter + rule_set.filter:
                if rule.extract:
                    variables[rule.extract.name] = ""
            for phase in PHASES:
                for index, rule in enumerate(getattr(rule_set, phase)):
                    location = f"processing.rules.{resource_type}.{phase}[{index}]"
                    for expression in self._expressions(rule):
                        try:
                            # finalize runs per Bundle, without the variables of the filter phase
                            evaluate(dummy, expression, {} if phase == "finalize" else variables, FHIR_MODEL,
                                     options={"userInvocationTable": self.functions})
                        except Exception as e:
                            problems.append(f"{location}: '{expression}': {e}")
        return problems

    @staticmethod
    def _expressions(rule: Rule) -> List[str]:
        expressions = []
        if rule.skip and rule.skip.when:
            expressions.append(rule.skip.when)
        if rule.extract:
            expressions.append(rule.extract.from_)
        templates = []
        if rule.set and isinstance(rule.set.value, str):
            templates.append(rule.set.value)
        if rule.set_identifier:
            templates += [rule.set_identifier.system, rule.set_identifier.value]
        for template in templates:
            expressions += [m.group(1) for m in PLACEHOLDER.finditer(template) if m.group(1)]
        return expressions

    # -- application ---------------------------------------------------------------------------------------------

    def apply(self, phase: str, resource: Resource, variables: Dict[str, str]) -> Optional[Resource]:
        """Apply the rules of a phase to a resource. Returns the changed resource, or None if it is skipped.

        Variables extracted in the filter phase are added to 'variables' for the following phases.
        """
        resource_type = resource.get_resource_type()
        rules = [(key, index, rule) for key in ("*", resource_type) if key in self.rules
                 for index, rule in enumerate(getattr(self.rules[key], phase))]
        if not rules:
            return resource
        data = json.loads(resource.model_dump_json())
        for key, index, rule in rules:
            location = f"processing.rules.{key}.{phase}[{index}]"
            try:
                data = self._apply_rule(rule, data, variables, location)
            except Exception as e:
                if isinstance(e, RuleError) and str(e).startswith("processing.rules"):
                    raise
                raise RuleError(f"{location} failed for {resource_type}/{data.get('id')}: {e}") from e
            if data is None:
                if phase != "filter":
                    raise RuleError(f"{location} removed {resource_type}/{resource.id}, resources can only be "
                                    f"skipped in the filter phase")
                return None
        try:
            return get_fhir_model_class(resource_type).model_validate(data)
        except Exception as e:
            raise RuleError(f"{resource_type}/{data.get('id')} is no valid resource after the {phase} rules: {e}") \
                from None

    def _apply_rule(self, rule: Rule, data: Dict, variables: Dict[str, str], location: str) -> Optional[Dict]:
        reference = f"{data['resourceType']}/{data.get('id')}"
        if rule.skip:
            if rule.skip.when is None or self._is_true(data, rule.skip.when, variables):
                self._log(rule.skip.log, f"{reference} skipped by {location}"
                          + (f" ({rule.skip.when})" if rule.skip.when else ""))
                return None
        elif rule.extract:
            value = self._first(data, rule.extract.from_, variables)
            match = re.search(rule.extract.pattern, value) if rule.extract.pattern and value is not None else None
            if rule.extract.pattern:
                value = (match.group(1) if match.groups() else match.group(0)) if match else None
            if value is None:
                message = (f"{reference}: '{rule.extract.from_}' gives no value"
                           + (f" matching '{rule.extract.pattern}'" if rule.extract.pattern else "")
                           + f" for variable {rule.extract.name} ({location})")
                if rule.extract.on_mismatch == "error":
                    raise RuleError(message)
                if rule.extract.on_mismatch == "skip":
                    logger.warning(f"{message}. Skipping the resource.")
                    return None
                value = ""
            variables[rule.extract.name] = value
        elif rule.set:
            value = rule.set.value
            if isinstance(value, str):
                value = self._render(data, value, variables, location)
            self._set(data, parse_path(rule.set.path), value)
        elif rule.delete is not None:
            for path in rule.paths_to_delete():
                self._delete(data, parse_path(path))
        elif rule.set_identifier:
            identifier = {"system": self._render(data, rule.set_identifier.system, variables, location),
                          "value": self._render(data, rule.set_identifier.value, variables, location)}
            field = get_fhir_model_class(data["resourceType"]).model_fields.get("identifier")
            if field is None:
                raise RuleError(f"{data['resourceType']} has no identifier element")
            data["identifier"] = [identifier] if "List" in str(field.annotation) else identifier
        elif rule.python:
            data = self._function(rule.python.function)(data, variables)
        return data

    def _evaluate(self, data: Dict, expression: str, variables: Dict[str, str]) -> List[Any]:
        return evaluate(data, expression, variables, FHIR_MODEL, options={"userInvocationTable": self.functions})

    def _first(self, data: Dict, expression: str, variables: Dict[str, str]) -> Optional[str]:
        result = self._evaluate(data, expression, variables)
        return str(result[0]) if result else None

    def _is_true(self, data: Dict, expression: str, variables: Dict[str, str]) -> bool:
        return self._evaluate(data, expression, variables) == [True]

    def _render(self, data: Dict, template: str, variables: Dict[str, str], location: str) -> str:
        def replace(match: re.Match) -> str:
            if match.group(0) in ("{{", "}}"):
                return match.group(0)[0]
            value = self._first(data, match.group(1), variables)
            if value is None:
                raise RuleError(f"{location}: '{{{match.group(1)}}}' gives no value for "
                                f"{data['resourceType']}/{data.get('id')}")
            return value
        return PLACEHOLDER.sub(replace, template)

    @staticmethod
    def _set(data: Dict, steps: List[Union[str, int]], value: Any) -> None:
        node: Any = data
        for step, following in zip(steps, steps[1:]):
            container = [] if isinstance(following, int) else {}
            if isinstance(step, int):
                if step == len(node):
                    node.append(container)
                node = node[step]
            else:
                node = node.setdefault(step, container)
        last = steps[-1]
        if isinstance(last, int):
            if last == len(node):
                node.append(value)
            else:
                node[last] = value
        else:
            node[last] = value

    @staticmethod
    def _delete(data: Dict, steps: List[Union[str, int]]) -> None:
        node: Any = data
        for step in steps[:-1]:
            try:
                node = node[step]
            except (KeyError, IndexError, TypeError):
                return
        last = steps[-1]
        try:
            del node[last]
        except (KeyError, IndexError, TypeError):
            pass

    @staticmethod
    def _log(level: str, message: str) -> None:
        if level == "warning":
            logger.warning(message)
        elif level == "info":
            logger.info(message)

    @staticmethod
    def _function(name: str) -> Callable[[Dict, Dict[str, str]], Optional[Dict]]:
        module, _, function = name.partition(":")
        return getattr(importlib.import_module(module), function)
