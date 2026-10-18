import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Union

import requests.exceptions
from dic_helpers.fhir_client import FHIRClient
from fhir.resources.R4B.bundle import Bundle, BundleEntry, BundleEntryRequest
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.resource import Resource

from fhirsync.config import Config, Source, Target
from fhirsync.rules import RuleEngine
from fhirsync.sources import FileSource, ServerSource

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """What a fetch_resources() run wrote."""
    # Bundles written to files or accepted by the target server
    bundles: int = 0
    # Entries in these Bundles: search results and their referenced resources
    entries: int = 0
    # Bundles the target server rejected with 404 (logged, not counted in bundles and entries)
    rejected: int = 0


class MissingReferenceError(Exception):
    """A reference whose target the source doesn't have, with on_missing_reference error."""


class ResourceBundler:
    """
    A class for bundling and processing FHIR resources.
    """

    def __init__(self, config: Config) -> None:
        """
        Initialize the bundler: create the source (a server client, or all files read), the target client (with
        target mode server) and the rule engine. With OAuth 2.0, the clients already fetch their tokens here.

        :param config: The validated fhirsync configuration.
        :type config: Config
        """
        self.config = config
        self.source: Union[ServerSource, FileSource]
        if config.source.type == "server":
            self.source_client: Optional[FHIRClient] = self._client(config.source,
                                                                    next_param=config.source.next_param)
            self.source = ServerSource(self.source_client)
        else:
            self.source_client = None
            self.source = FileSource(config.source)
        self.target_client = self._client(config.target) if config.target.mode == "server" else None
        self.rules: RuleEngine = config.processing.engine()

    def _client(self, section: Union[Source, Target], **options: Any) -> FHIRClient:
        """
        Create a FHIR client for the source or target section.

        :param section: The configuration section of the endpoint.
        :type section: Union[Source, Target]
        :param options: Additional client options, None values are left to the client's defaults.
        :return: The FHIR client.
        :rtype: FHIRClient
        """
        ca_file, rfc5280 = self.config.tls(section)
        auth = section.auth.model_dump(exclude_none=True) if section.auth else {}
        options = {key: value for key, value in options.items() if value is not None}
        return FHIRClient(base_url=section.base_url, ca_file=ca_file, rfc5280=rfc5280, **auth, **options)

    def run(self, searches: Optional[List[str]] = None) -> SyncResult:
        """
        Run what the configuration describes: for a server source every search, for a files source all resources of
        the files (of the configured resource_types).

        :param searches: Searches instead of source.searches, e.g. from -q; only for a server source.
        :type searches: Optional[List[str]]
        :return: The number of Bundles and entries written, added up over all searches.
        :rtype: SyncResult
        :raises ValueError: For searches with a files source, or a server source without searches.
        """
        if isinstance(self.source, FileSource):
            if searches:
                raise ValueError("Searches need a source of type server.")
            return self._bundle_results(self.source.pages())
        searches = searches or self.config.source.searches
        if not searches:
            raise ValueError("No searches: configure source.searches or give a search.")
        total = SyncResult()
        for search in searches:
            result = self.fetch_resources(search)
            total.bundles += result.bundles
            total.entries += result.entries
            total.rejected += result.rejected
        return total

    def fetch_resources(self, search_path: str,
                        params: Optional[Dict[str, Union[str, List[str]]]] = None) -> SyncResult:
        """
        Search the source, process the results with their references and write the Bundles to the target.

        :param search_path: The search, '<Type>' or '<Type>?<parameters>', the parameters not URL encoded.
        :type search_path: str
        :param params: More search parameters, not encoded, a list value repeats the parameter. They take precedence
            over the parameters of search_path, which take precedence over source.request_params.
        :type params: Optional[Dict[str, Union[str, List[str]]]]
        :return: The number of Bundles and entries written.
        :rtype: SyncResult
        :raises ValueError: For a files source, which has no searches.
        """
        if not isinstance(self.source, ServerSource):
            raise ValueError("Searches need a source of type server.")
        if "?" in search_path:
            search_resource_type, search_params = search_path.split("?", maxsplit=1)
            params = (self.get_request_params(search_resource_type) | self.parse_query_params(search_params)
                      | (params or {}))
        else:
            search_resource_type = search_path
            params = self.get_request_params(search_resource_type) | (params or {})
        return self._bundle_results(self.source.pages(search_resource_type, params))

    def _bundle_results(self, pages: Iterator[List[Resource]]) -> SyncResult:
        """
        Process the results with their references, put them into Bundles of bundle.size results and write the
        Bundles to the target. With bundle.size 0, every page is one Bundle.

        :param pages: The results, as pages: search result pages, or the files of a files source.
        :type pages: Iterator[List[Resource]]
        :return: The number of Bundles and entries written.
        :rtype: SyncResult
        """
        logger.info("Started Processing")
        result = SyncResult()
        for page in pages:
            if not page:
                logger.debug("Ignoring empty page")
                continue

            current_bundle = None
            bundle_settings = self.config.bundling.bundle
            max_bundle_size = bundle_settings.size

            for num, resource in enumerate(page):
                # Initialize an empty Bundle if needed
                if current_bundle is None:
                    current_bundle = Bundle(
                        type=bundle_settings.type,
                        entry=[],
                        identifier=Identifier(system=bundle_settings.identifier_system,
                                              value=str(uuid.uuid4()))
                    )

                # Process entry and add to bundle
                for processed_entry in self.process_entry(BundleEntry(resource=resource)):
                    if not self.resource_exists_in_bundle(current_bundle, processed_entry.resource):
                        current_bundle.entry.append(processed_entry)
                    else:
                        logger.info(f"Resource {processed_entry.resource.get_resource_type()}/{processed_entry.resource.id} already exists in Bundle.")

                if max_bundle_size > 0 and (num + 1) % max_bundle_size == 0 and len(current_bundle.entry) > 0:
                    current_bundle = self.post_process_bundle(current_bundle)
                    logger.debug(f"Sending bundle {num} to output.")
                    self._handle_bundle_output(current_bundle, result)
                    current_bundle = None

            # Handle remaining entries after loop
            if current_bundle is not None and len(current_bundle.entry) > 0:
                logger.debug("Sending last bundle to output.")
                current_bundle = self.post_process_bundle(current_bundle)
                self._handle_bundle_output(current_bundle, result)
        logger.info(f"Finished Processing: {result.bundles} Bundles with {result.entries} entries written, "
                    f"{result.rejected} rejected")
        return result

    def _handle_bundle_output(self, bundle: Bundle, result: SyncResult) -> None:
        """
        Handle the output of a processed bundle.

        :param bundle: The bundle to be handled.
        :type bundle: Bundle
        :param result: The result of the run, counts the Bundle.
        :type result: SyncResult
        """
        if self.config.target.mode == "file":
            self._write_bundle_to_file(bundle)
            accepted = True
        else:
            accepted = self._send_bundle_to_server(bundle)
        if accepted:
            result.bundles += 1
            result.entries += len(bundle.entry)
        else:
            result.rejected += 1

    def _write_bundle_to_file(self, bundle: Bundle) -> None:
        """
        Write a bundle to a file.

        :param bundle: The bundle to be written to a file.
        :type bundle: Bundle
        """
        outdir = self.config.target.outdir
        if not os.path.exists(outdir):
            os.mkdir(outdir)
        if len(bundle.entry) > 0:
            path = os.path.join(outdir, f'{bundle.identifier.value}.json')
            logger.info(f"Writing bundle with {len(bundle.entry)} entries to file {path}.")
            with open(path, 'w', encoding='utf-8') as outfile:
                outfile.truncate(0)
                outfile.write(bundle.model_dump_json())
        else:
            logger.warning("Bundle did not contain any resource. This may occur if all resources are filtered. "
                           "No output file written.")

    def _send_bundle_to_server(self, bundle: Bundle) -> bool:
        """
        Send a bundle to the target server.

        :param bundle: The bundle to be sent to the server.
        :type bundle: Bundle
        :return: False if the target rejected the Bundle with 404, True if it accepted it.
        :rtype: bool
        """
        logger.info(f"Sending bundle with {len(bundle.entry)} entries to target server at "
                    f"{self.target_client.base_url}...")
        try:
            self.target_client.post_bundle(bundle)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning(f"Target server returned 404 for bundle {bundle.identifier.value}, most likely because "
                               f"a referenced Patient does not exist on the target. Server response: "
                               f"{self._describe_error_response(e.response)}")
                return False
            raise
        return True

    @staticmethod
    def _describe_error_response(response: requests.Response) -> str:
        """
        Build a readable description of an error response, using the issues of an OperationOutcome if present.

        :param response: The error response.
        :type response: requests.Response
        :return: A description of the error response.
        :rtype: str
        """
        try:
            body = response.json()
        except ValueError:
            return response.text[:500] or "<empty body>"
        if isinstance(body, dict) and body.get("resourceType") == "OperationOutcome":
            return "; ".join(f"{issue.get('severity')}: "
                             f"{issue.get('diagnostics') or (issue.get('details') or {}).get('text')}"
                             for issue in body.get("issue", []))
        return response.text[:500]

    def post_process_bundle(self, bundle: Bundle) -> Bundle:
        """
        Post-process a bundle by applying post-processing to each resource.

        :param bundle: The bundle to be post-processed.
        :type bundle: Bundle
        :return: The post-processed bundle.
        :rtype: Bundle
        """
        for idx, entry in enumerate(bundle.entry):
            resource = self.post_process_resource(entry.resource)
            request = bundle.entry[idx].request
            bundle.entry[idx] = BundleEntry(resource=resource, request=request)
        return bundle

    def post_process_resource(self, resource: Resource) -> Resource:
        """
        Post-process a single resource after its Bundle entry request is built, by applying the finalize rules.

        :param resource: The resource to be post-processed.
        :type resource: Resource
        :return: The post-processed resource.
        :rtype: Resource
        """
        return self.rules.apply("finalize", resource, {})

    def process_entry(self, entry: BundleEntry) -> Iterator[BundleEntry]:
        """
        Process a single entry from a bundle.

        :param entry: The entry to be processed.
        :type entry: BundleEntry
        :return: An iterator of processed BundleEntry objects.
        :rtype: Iterator[BundleEntry]
        """
        logger.info(f"Processing entry {entry.resource.get_resource_type()}/{entry.resource.id}")
        for resource in self.process_resource(entry.resource):
            if not resource.get_resource_type() == "OperationOutcome":
                request = self.create_entry_request(resource)
                yield BundleEntry(resource=resource, request=request)
            else:
                logger.warning(resource.diagnostics)

    def process_resource(self, resource: Resource) -> Iterator[Resource]:
        """
        Process a single resource, including handling references and specific resource types.

        :param resource: The resource to be processed.
        :type resource: Resource
        :return: An iterator of processed Resource objects.
        :rtype: Iterator[Resource]
        """
        logger.info(f"Processing resource {resource.get_resource_type()}/{resource.id}")

        if resource.get_resource_type() == "OperationOutcome":
            for issue in resource.issue:
                logger.warning(f"OperationOutcome: {issue.severity} - {issue.diagnostics}")
            return

        # Variables extracted by the filter rules, available to the transform rules
        variables: Dict[str, str] = {}
        resource = self.rules.apply("filter", resource, variables)
        if resource is None:
            return

        bundling = self.config.bundling
        resource_type = resource.get_resource_type()
        references = self.extract_references(resource)
        for reference in references:
            if reference.get('resource_type') not in bundling.ignore_references_to_for(resource_type):
                for referenced_resource in self.fetch_reference(reference, resource):
                    for processed_reference_resource in self.process_resource(referenced_resource):
                        if bundling.reference_mode_for(resource_type) == 'identifier':
                            if reference.get('type') != 'logical' or 'identifier' not in reference.get('params').keys():
                                identifier = self._identifier(processed_reference_resource)
                                new_reference = f"{processed_reference_resource.get_resource_type()}?identifier={identifier.system}|{identifier.value}"
                                resource = self.update_reference(resource, reference.get('path'), new_reference)
                        yield processed_reference_resource

            else:
                logger.info(f"Reference of type {reference.get('resource_type')} ignored.")

        resource = self.rules.apply("transform", resource, variables)

        yield resource

    def fetch_reference(self, reference: Dict[str, Any], referencing: Resource) -> Iterator[Any]:
        """
        Fetch a referenced resource from the source. A reference the source doesn't have is handled according to
        on_missing_reference of the referencing resource's type.

        :param reference: A dictionary containing reference information, from extract_references().
        :type reference: Dict[str, Any]
        :param referencing: The resource that contains the reference.
        :type referencing: Resource
        :return: The referenced resource; for a Type?<parameters> reference the search result pages (known
            limitation, docs/rules.md section 8); nothing if the source doesn't have it and on_missing_reference
            is warning.
        :rtype: Iterator[Any]
        :raises MissingReferenceError: If the source doesn't have it and on_missing_reference is error.
        """
        if reference.get('type') == 'logical':
            logger.info(f"Searching for reference {reference}...")
            params = reference.get('params') | self.get_request_params(reference.get('resource_type'))
            pages = self.source.search(reference.get('resource_type'), params)
            if not pages:
                self._missing_reference(reference, referencing)
            yield from pages
        elif reference.get('type') == 'canonical':
            logger.info(f"Fetching reference {reference.get('resource_type')}/{reference.get('resource_id')}...")
            referenced_resource = self.source.read(reference.get('resource_type'), reference.get('resource_id'))
            if referenced_resource is None:
                self._missing_reference(reference, referencing)
            else:
                yield referenced_resource
        else:
            raise ValueError(f"Unknown reference type {reference.get('type')}")

    def _missing_reference(self, reference: Dict[str, Any], referencing: Resource) -> None:
        """
        Handle a reference the source doesn't have: raise with on_missing_reference error, otherwise log a warning.
        The reference stays unchanged, as nothing is fetched for it.

        :param reference: A dictionary containing reference information, from extract_references().
        :type reference: Dict[str, Any]
        :param referencing: The resource that contains the reference.
        :type referencing: Resource
        :raises MissingReferenceError: With on_missing_reference error.
        """
        if reference.get('type') == 'canonical':
            target = f"{reference.get('resource_type')}/{reference.get('resource_id')}"
        else:
            target = f"{reference.get('resource_type')}?" + "&".join(
                f"{name}={value}" for name, value in reference.get('params').items())
        resource_type = referencing.get_resource_type()
        message = f"Reference {target} of {resource_type}/{referencing.id} not found in the source"
        if self.config.bundling.on_missing_reference_for(resource_type) == "error":
            raise MissingReferenceError(f"{message}. Set bundling.resources.{resource_type}.on_missing_reference to "
                                        f"warning to continue without it.")
        logger.warning(f"{message}, the reference is left unchanged.")

    @staticmethod
    def resource_exists_in_bundle(bundle: Bundle, resource: Resource) -> bool:
        """
        Check if a resource already exists in a bundle.

        :param bundle: The bundle to check.
        :type bundle: Bundle
        :param resource: The resource to look for.
        :type resource: Resource
        :return: True if the resource exists in the bundle, False otherwise.
        :rtype: bool
        """
        return any(
            entry.resource.get_resource_type() == resource.get_resource_type() and entry.resource.id == resource.id
            for entry in bundle.entry
        )

    @staticmethod
    def extract_references(resource: Resource) -> list[Dict[str, Any]]:
        """
        Extract references from a FHIR resource with their exact paths.

        :param resource: FHIR resource to extract references from
        :type resource: Resource
        :return: List of reference dictionaries with path information
        :rtype: List[dict]
        """
        references = []
        resource_dict = resource.model_dump()

        def _is_reference(value):
            """
            Whether a JSON value is a FHIR Reference with a reference text.

            :param value: Any JSON value of the resource.
            :return: True for an object with a 'reference' string.
            :rtype: bool
            """
            return isinstance(value, dict) and 'reference' in value and isinstance(value['reference'], str)

        def _extract_from_dict(data, current_path):
            """
            Add the references of a JSON object and of everything below it to references.

            :param data: A JSON object of the resource.
            :param current_path: The path of data in the resource, e.g. ['extension', 0].
            """
            for key, value in data.items():
                new_path = current_path + [key]
                if _is_reference(value):
                    _add_reference(value['reference'], new_path)
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        # A list of References, e.g. Observation.performer, or of objects that contain References
                        if _is_reference(item):
                            _add_reference(item['reference'], new_path + [index])
                        elif isinstance(item, dict):
                            _extract_from_dict(item, new_path + [index])
                elif isinstance(value, dict):
                    _extract_from_dict(value, new_path)

        def _add_reference(ref, path):
            """
            Add a reference to references: Type/id as canonical, Type?parameters as logical, anything else is logged
            as malformed and left alone.

            :param ref: The reference text.
            :param path: The path of the Reference in the resource, e.g. ['performer', 0].
            """
            try:
                if '/' in ref and ref.split('/')[1]:
                    references.append({
                        'type': 'canonical',
                        'resource_type': ref.split('/')[0],
                        'resource_id': ref.split('/')[1],
                        'path': path
                    })
                elif '?' in ref:
                    references.append({
                        'type': 'logical',
                        'resource_type': ref.split('?')[0],
                        'params': dict(param.split('=', 1) for param in ref.split('?')[1].split('&')),
                        'path': path
                    })
                else:
                    raise ValueError(f"Malformed reference '{ref}' at path {path}")
            except Exception as e:
                logger.error(f"Error processing reference in {resource.id}: {str(e)}")

        _extract_from_dict(resource_dict, [])
        if references:
            logger.info(f"Found {len(references)} references in resource {resource.id}")
        return references

    @staticmethod
    def update_reference(resource: Resource, path: List[str | int], new_reference: str) -> Resource:
        """
        Update a reference in a FHIR Resource and return a new updated instance.

        :param resource: Original FHIR Resource to modify.
        :type resource: Resource
        :param path: Path array from reference metadata (e.g. ['entry', 0, 'resource', 'subject']).
        :type path: List[str | int]
        :param new_reference: New reference value to set (e.g. 'Patient/456').
        :type new_reference: str
        :return: New updated Resource instance.
        :rtype: Resource
        """
        current_node = resource
        steps = path[:-1]
        final_key = path[-1]

        try:
            for step in steps:
                if isinstance(step, int):
                    current_node = current_node[step]
                else:
                    current_node = getattr(current_node, step)

            final_obj = current_node
            if isinstance(final_key, int):
                final_obj[final_key].reference = new_reference
            else:
                ref_attr = getattr(final_obj, final_key)
                ref_attr.reference = new_reference

        except (IndexError, AttributeError, TypeError) as e:
            raise ValueError(f"Invalid path {path} for resource {resource.id}") from e

        return resource

    def _identifier(self, resource: Resource) -> Identifier:
        """
        The identifier that counts for a resource's request and for references to it, according to identifier_system
        and on_missing_identifier_system of its type.

        :param resource: The resource.
        :type resource: Resource
        :return: The identifier.
        :rtype: Identifier
        :raises ValueError: See get_primary_identifier().
        """
        resource_type = resource.get_resource_type()
        bundling = self.config.bundling
        return self.get_primary_identifier(resource, bundling.identifier_system_for(resource_type),
                                           bundling.on_missing_identifier_system_for(resource_type))

    @staticmethod
    def get_primary_identifier(resource: Resource, system: Optional[str] = None,
                               on_missing_system: str = "error") -> Identifier:
        """
        Get the identifier of a FHIR resource: the one with the given system, or the first one without a system.

        :param resource: FHIR resource to get identifier from.
        :type resource: Resource
        :param system: Identifier system to match (optional). For resource types with a single identifier, it must
            have this system.
        :type system: Optional[str]
        :param on_missing_system: No identifier with the system: 'error' raises, 'first' logs a warning and returns
            the first identifier.
        :type on_missing_system: str
        :return: Identifier of the FHIR resource.
        :rtype: Identifier
        :raises ValueError: If the resource type has no identifier element, the resource has no identifier, or (with
            on_missing_system error) none with the system.
        """
        resource_type = resource.get_resource_type()
        resource_ref = f"{resource_type}/{resource.id}"
        if 'identifier' not in type(resource).model_fields:
            raise ValueError(f"Resource type {resource_type} has no identifier field ({resource_ref}).")
        if not resource.identifier:
            raise ValueError(f"Resource {resource_ref} has no identifier.")
        # Most resource types have a list of identifiers, some (e.g. QuestionnaireResponse) a single one
        identifiers = resource.identifier if isinstance(resource.identifier, list) else [resource.identifier]
        if system is None:
            return identifiers[0]
        for identifier in identifiers:
            if identifier.system == system:
                return identifier
        message = f"Resource {resource_ref} has no identifier with system {system}"
        if on_missing_system == "error":
            raise ValueError(f"{message}. Set bundling.resources.{resource_type}.on_missing_identifier_system to first "
                             f"to use its first identifier instead.")
        logger.warning(f"{message}, its first identifier ({identifiers[0].system}) is used.")
        return identifiers[0]

    def create_entry_request(self, resource: Resource) -> BundleEntryRequest:
        """
        Create a BundleEntryRequest for a given resource.

        :param resource: The resource to create a request for.
        :type resource: Resource
        :return: A BundleEntryRequest for the given resource.
        :rtype: BundleEntryRequest
        """
        resource_type = resource.get_resource_type()
        request = self.config.bundling.request_for(resource_type)
        method = request.method.upper()
        if_none_exist = None
        if method == 'POST':
            # A create always targets the resource type, the identifier is only used for the ifNoneExist condition
            url = resource_type
            if request.condition == 'ifNoneExist':
                identifier = self._identifier(resource)
                # Only the query part of the search URL, as defined for Bundle.entry.request.ifNoneExist
                if_none_exist = f"identifier={identifier.system}|{identifier.value}"
        else:
            if request.url == 'identifier':
                identifier = self._identifier(resource)
                url = f"{resource_type}?identifier={identifier.system}|{identifier.value}"
            else:
                url = f"{resource_type}/{resource.id}"

        return BundleEntryRequest(method=method, url=url, ifNoneExist=if_none_exist)

    def get_request_params(self, resource_type: str) -> Dict[str, Any]:
        """
        Get request parameters for a given resource type.

        :param resource_type: The type of resource to get parameters for.
        :type resource_type: str
        :return: A dictionary of request parameters.
        :rtype: Dict[str, Any]
        """
        params = self.config.source.request_params_for(resource_type)
        logger.debug(f"Request params for resource type {resource_type} are {params}")
        return params

    @staticmethod
    def parse_query_params(params_str: str) -> Dict[str, Any]:
        """
        Parses a string of query parameters into a dictionary.

        :param params_str: A string of parameters in the format "key1=value1&key2=value2".
        :type params_str: str
        :return: A dictionary of unpacked parameters.
        :rtype: Dict[str, Any]
        """
        return dict(param.split('=', 1) for param in params_str.split('&'))


