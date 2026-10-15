import os
import uuid
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Iterator, Union

import requests.exceptions
from dic_helpers.fhir_client import FHIRClient
from fhir.resources.R4B.resource import Resource
from fhir.resources.R4B.bundle import Bundle, BundleEntry, BundleEntryRequest
from fhir.resources.R4B.identifier import Identifier
from requests import HTTPError

from fhirsync.config import Config, Source, Target
from fhirsync.rules import RuleEngine

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


class ResourceBundler:
    """
    A class for bundling and processing FHIR resources.

    :param config: The validated fhirsync configuration.
    :type config: Config
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.source_client = self._client(config.source, next_param=config.source.next_param)
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
        """
        logger.info("Started Processing")
        result = SyncResult()
        if "?" in search_path:
            search_resource_type, search_params = search_path.split("?", maxsplit=1)
            params = (self.get_request_params(search_resource_type) | self.parse_query_params(search_params)
                      | (params or {}))
        else:
            search_resource_type = search_path
            params = self.get_request_params(search_resource_type) | (params or {})
        for bundle in self.source_client.search(search_resource_type, params=params):
            if bundle.entry is None:
                logger.debug(f"Ignoring empty bundle with id {bundle.id}")
                continue
            logger.debug(f"Fetched a bundle with {bundle.total} entries.")

            current_bundle = None
            bundle_settings = self.config.bundling.bundle
            max_bundle_size = bundle_settings.size

            for num, entry in enumerate(bundle.entry):
                # Initialize an empty Bundle if needed
                if current_bundle is None:
                    current_bundle = Bundle(
                        type=bundle_settings.type,
                        entry=[],
                        identifier=Identifier(system=bundle_settings.identifier_system,
                                              value=str(uuid.uuid4()))
                    )

                # Process entry and add to bundle
                for processed_entry in self.process_entry(entry):
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
                logger.debug(f"Sending last bundle to ouput.")
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
                for referenced_resource in self.fetch_reference(reference):
                    for processed_reference_resource in self.process_resource(referenced_resource):
                        if bundling.reference_mode_for(resource_type) == 'identifier':
                            if reference.get('type') != 'logical' or 'identifier' not in reference.get('params').keys():
                                identifier = self.get_primary_identifier(processed_reference_resource)
                                new_reference = f"{processed_reference_resource.get_resource_type()}?identifier={identifier.system}|{identifier.value}"
                                resource = self.update_reference(resource, reference.get('path'), new_reference)
                        yield processed_reference_resource

            else:
                logger.info(f"Reference of type {reference.get('resource_type')} ignored.")

        resource = self.rules.apply("transform", resource, variables)

        yield resource

    def fetch_reference(self, reference: Dict[str, Any]) -> Iterator[Resource]:
        """
        Fetch a referenced resource.

        :param reference: A dictionary containing reference information.
        :type reference: Dict[str, Any]
        :return: An iterator of fetched Resource objects.
        :rtype: Iterator[Resource]
        """
        if reference.get('type') == 'logical':
            logger.info(f"Searching for reference {reference}...")
            params = reference.get('params') | self.get_request_params(reference.get('resource_type'))
            reference_bundle = self.source_client.search(reference.get('resource_type'), params=params)
            for entry in reference_bundle:
                yield entry
        elif reference.get('type') == 'canonical':
            logger.info(f"Fetching reference {reference.get('resource_type')}/{reference.get('resource_id')}...")
            referenced_resource = self.source_client.get_resource(reference.get('resource_type'), reference.get('resource_id'))
            yield referenced_resource
        else:
            raise ValueError(f"Unknown reference type {reference.get('type')}")

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
            return isinstance(value, dict) and 'reference' in value and isinstance(value['reference'], str)

        def _extract_from_dict(data, current_path):
            for key, value in data.items():
                new_path = current_path + [key]
                if _is_reference(value):
                    ref = value['reference']
                    try:
                        if '/' in ref and ref.split('/')[1]:
                            references.append({
                                'type': 'canonical',
                                'resource_type': ref.split('/')[0],
                                'resource_id': ref.split('/')[1],
                                'path': new_path
                            })
                        elif '?' in ref:
                            references.append({
                                'type': 'logical',
                                'resource_type': ref.split('?')[0],
                                'params': dict(param.split('=', 1) for param in ref.split('?')[1].split('&')),
                                'path': new_path
                            })
                        else:
                            raise ValueError(f"Malformed reference '{ref}' at path {new_path}")
                    except Exception as e:
                        logger.error(f"Error processing reference in {resource.id}: {str(e)}")
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        if isinstance(item, (dict, list)):
                            _extract_from_dict(item, new_path + [index])
                elif isinstance(value, dict):
                    _extract_from_dict(value, new_path)

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

    @staticmethod
    def get_primary_identifier(resource: Resource, system: Optional[str] = None) -> Identifier:
        """
        Get the identifier of a FHIR resource.

        :param resource: FHIR resource to get identifier from.
        :type resource: Resource
        :param system: Identifier system to match (optional).
        :type system: Optional[str]
        :return: Identifier of the FHIR resource.
        :rtype: Identifier
        :raises ValueError: If the resource has no identifier.
        """
        resource_ref = f"{resource.get_resource_type()}/{resource.id}"
        identifier_field = resource.model_fields.get('identifier')
        if not identifier_field:
            raise ValueError(f"Resource type {resource.get_resource_type()} has no identifier field ({resource_ref}).")
        if not resource.identifier:
            raise ValueError(f"Resource {resource_ref} has no identifier.")
        if 'List' in str(identifier_field.annotation):
            if system is not None:
                for identifier in resource.identifier:
                    if identifier.system == system:
                        return identifier
                logger.warning(f"Identifier system {system} was not found in resource {resource_ref}. Falling back "
                               f"to first identifier entry.")
            return resource.identifier[0]
        elif 'Identifier' in str(identifier_field.annotation):
            if system is not None and resource.identifier.system != system:
                logger.warning(f"Identifier system {system} was not found in resource {resource_ref}. Using its "
                               f"only identifier.")
            return resource.identifier
        else:
            raise ValueError(f"Unknown identifier field data type in resource type {resource.get_resource_type()}.")

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
                identifier = self.get_primary_identifier(resource)
                # Only the query part of the search URL, as defined for Bundle.entry.request.ifNoneExist
                if_none_exist = f"identifier={identifier.system}|{identifier.value}"
        else:
            if request.url == 'identifier':
                identifier = self.get_primary_identifier(resource)
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


