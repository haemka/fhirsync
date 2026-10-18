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
        """
        Check the variable name and compile the pattern, so that invalid ones are reported at startup.

        :return: The validated rule.
        :rtype: Extract
        :raises ValueError: For an invalid variable name or regular expression.
        """
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
        """
        Check the element path.

        :return: The validated rule.
        :rtype: Set
        :raises ValueError: For an invalid element path.
        """
        check_path(self.path)
        return self


class SetIdentifier(Model):
    system: str                                 # template
    value: str                                  # template


class Python(Model):
    function: str                               # module:function, called with (resource dict, variables dict)

    @model_validator(mode="after")
    def check(self) -> "Python":
        """
        Check that the function is given as module:function. Whether it exists is only checked when it is called.

        :return: The validated rule.
        :rtype: Python
        :raises ValueError: If the colon is missing.
        """
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
        """
        Accept 'skip:' (null in YAML) like 'skip: {}': both mean skip unconditionally.

        :param data: The rule as read from the configuration.
        :type data: Any
        :return: The rule, with an empty skip mapping instead of null.
        :rtype: Any
        """
        if isinstance(data, dict) and "skip" in data and data["skip"] is None:
            data = {**data, "skip": {}}
        return data

    @model_validator(mode="after")
    def check(self) -> "Rule":
        """
        Check that the rule has exactly one operation, and that the element paths of delete are valid.

        :return: The validated rule.
        :rtype: Rule
        :raises ValueError: For none or several operations, or an invalid element path.
        """
        given = self.operations()
        if len(given) != 1:
            raise ValueError(f"a rule needs exactly one operation of {', '.join(Rule.model_fields)}, "
                             f"got {', '.join(given) or 'none'}")
        if self.delete is not None:
            for path in self.paths_to_delete():
                check_path(path)
        return self

    def operations(self) -> List[str]:
        """
        The operations given in this rule.

        :return: The names of the operations that are set, e.g. ['set'].
        :rtype: List[str]
        """
        return [name for name in Rule.model_fields if getattr(self, name) is not None]

    def operation(self) -> str:
        """
        The operation of this rule, after check() made sure there is exactly one.

        :return: The name of the operation, e.g. 'set'.
        :rtype: str
        """
        return self.operations()[0]

    def paths_to_delete(self) -> List[str]:
        """
        The element paths of a delete rule, also when a single path is given as text.

        :return: The element paths.
        :rtype: List[str]
        """
        return [self.delete] if isinstance(self.delete, str) else list(self.delete)


class RuleSet(Model):
    """Rules of one resource type, per phase."""
    filter: List[Rule] = []
    transform: List[Rule] = []
    finalize: List[Rule] = []

    @model_validator(mode="after")
    def check_phases(self) -> "RuleSet":
        """
        Check that every rule's operation is allowed in its phase (PHASE_OPERATIONS).

        :return: The validated rule set.
        :rtype: RuleSet
        :raises ValueError: For an operation that is not allowed in its phase.
        """
        for phase in PHASES:
            for index, rule in enumerate(getattr(self, phase)):
                if rule.operation() not in PHASE_OPERATIONS[phase]:
                    raise ValueError(f"{phase}[{index}]: {rule.operation()} is not allowed in phase {phase}, "
                                     f"allowed are {', '.join(PHASE_OPERATIONS[phase])}")
        return self


def check_path(path: str) -> None:
    """
    Check an element path of set or delete, e.g. patient.reference or meta.profile[0].

    :param path: The element path.
    :type path: str
    :raises ValueError: If it isn't a valid element path.
    """
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
    """Applies the configured rules to resources."""

    def __init__(self, rules: Dict[str, RuleSet], identifier_systems: Dict[str, str]) -> None:
        """
        Initialize the engine with the rules and the fhirsync functions for FHIRPath.

        :param rules: Rule sets per resource type, '*' for all types.
        :type rules: Dict[str, RuleSet]
        :param identifier_systems: Identifier systems per resource type, for identifierSystem('<Type>').
        :type identifier_systems: Dict[str, str]
        """
        self.rules = rules
        self.identifier_systems = identifier_systems
        self.functions = {
            "identifierSystem": {"fn": self._identifier_system, "arity": {1: ["String"]}},
            "sha256": {"fn": lambda inputs: [hashlib.sha256(str(i).encode()).hexdigest() for i in inputs],
                       "arity": {0: []}},
        }

    def _identifier_system(self, inputs: List[Any], resource_type: str) -> List[str]:
        """
        The FHIRPath function identifierSystem('<Type>'): the identifier system configured for a resource type.

        :param inputs: The input collection of the function call, not used.
        :type inputs: List[Any]
        :param resource_type: The resource type given as argument.
        :type resource_type: str
        :return: The identifier system, as a FHIRPath collection.
        :rtype: List[str]
        :raises RuleError: If no identifier system is configured for the type.
        """
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
        """
        All FHIRPath expressions of a rule: 'when', 'from', and the expressions in braces of its templates.

        :param rule: The rule.
        :type rule: Rule
        :return: The expressions.
        :rtype: List[str]
        """
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
        """
        Apply one rule to a resource.

        :param rule: The rule.
        :type rule: Rule
        :param data: The resource as JSON dict, changed in place by set, delete and set_identifier.
        :type data: Dict
        :param variables: The variables; extract adds to them.
        :type variables: Dict[str, str]
        :param location: The rule's place in the configuration, e.g. processing.rules.Consent.transform[1].
        :type location: str
        :return: The resource, or None if the rule skipped it.
        :rtype: Optional[Dict]
        :raises RuleError: For an extract without value and on_mismatch error, a template without value, or
            set_identifier on a resource type without identifier.
        """
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
        """
        Evaluate a FHIRPath expression on a resource, with the variables and the fhirsync functions.

        :param data: The resource as JSON dict.
        :type data: Dict
        :param expression: The FHIRPath expression.
        :type expression: str
        :param variables: The variables, available as %name.
        :type variables: Dict[str, str]
        :return: The result collection.
        :rtype: List[Any]
        """
        return evaluate(data, expression, variables, FHIR_MODEL, options={"userInvocationTable": self.functions})

    def _first(self, data: Dict, expression: str, variables: Dict[str, str]) -> Optional[str]:
        """
        The first result of a FHIRPath expression, as text.

        :param data: The resource as JSON dict.
        :type data: Dict
        :param expression: The FHIRPath expression.
        :type expression: str
        :param variables: The variables, available as %name.
        :type variables: Dict[str, str]
        :return: The first result as text, None for an empty result.
        :rtype: Optional[str]
        """
        result = self._evaluate(data, expression, variables)
        return str(result[0]) if result else None

    def _is_true(self, data: Dict, expression: str, variables: Dict[str, str]) -> bool:
        """
        Whether a FHIRPath condition is true: its result must be exactly [true], an empty result is false.

        :param data: The resource as JSON dict.
        :type data: Dict
        :param expression: The FHIRPath condition.
        :type expression: str
        :param variables: The variables, available as %name.
        :type variables: Dict[str, str]
        :return: True if the condition is true.
        :rtype: bool
        """
        return self._evaluate(data, expression, variables) == [True]

    def _render(self, data: Dict, template: str, variables: Dict[str, str], location: str) -> str:
        """
        Fill a template: every {expression} is replaced by its first result, {{ and }} are literal braces.

        :param data: The resource as JSON dict.
        :type data: Dict
        :param template: The template.
        :type template: str
        :param variables: The variables, available as %name.
        :type variables: Dict[str, str]
        :param location: The rule's place in the configuration, for the error message.
        :type location: str
        :return: The filled template.
        :rtype: str
        :raises RuleError: If an expression has no result.
        """
        def replace(match: re.Match) -> str:
            """
            The replacement for one placeholder.

            :param match: The match of PLACEHOLDER: '{{', '}}' or '{expression}'.
            :type match: re.Match
            :return: The literal brace, or the first result of the expression.
            :rtype: str
            :raises RuleError: If the expression has no result.
            """
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
        """
        Set an element, creating the elements and lists on the way. An index one past the end appends.

        :param data: The resource as JSON dict, changed in place.
        :type data: Dict
        :param steps: The element path from parse_path(), e.g. ['meta', 'profile', 0].
        :type steps: List[Union[str, int]]
        :param value: The value to set.
        :type value: Any
        """
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
        """
        Delete an element; nothing happens if it doesn't exist.

        :param data: The resource as JSON dict, changed in place.
        :type data: Dict
        :param steps: The element path from parse_path(), e.g. ['meta', 'lastUpdated'].
        :type steps: List[Union[str, int]]
        """
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
        """
        Log the message of a skip rule at its configured level.

        :param level: 'warning', 'info' or 'none' (not logged).
        :type level: str
        :param message: The message.
        :type message: str
        """
        if level == "warning":
            logger.warning(message)
        elif level == "info":
            logger.info(message)

    @staticmethod
    def _function(name: str) -> Callable[[Dict, Dict[str, str]], Optional[Dict]]:
        """
        Import the function of a python rule.

        :param name: The function as module:function.
        :type name: str
        :return: The function, called with the resource dict and the variables.
        :rtype: Callable[[Dict, Dict[str, str]], Optional[Dict]]
        :raises ImportError: If the module can't be imported.
        :raises AttributeError: If the module has no such function.
        """
        module, _, function = name.partition(":")
        return getattr(importlib.import_module(module), function)
