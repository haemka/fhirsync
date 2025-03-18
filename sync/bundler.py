import os
import uuid
import logging
import hashlib
from configparser import ConfigParser
from typing import List, Dict, Any, Optional, Iterator
from configparser import ConfigParser
from dic_helpers.fhir_client import FHIRClient
from fhir.resources.R4B.resource import Resource
from fhir.resources.R4B.bundle import Bundle, BundleEntry, BundleEntryRequest
from fhir.resources.R4B.identifier import Identifier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ResourceBundler:
    """
    A class for bundling and processing FHIR resources.

    :param config: Configuration object containing settings for the ResourceBundler.
    :type config: ConfigParser
    """

    def __init__(self, config: ConfigParser) -> None:
        self.config = config
        self.source_client = FHIRClient(base_url=self.config.get('source', 'base_url'),
                                        username=self.config.get('source', 'username'),
                                        password=self.config.get('source', 'password'),
                                        auth_url=self.config.get('source', 'auth_url'),
                                        realm=self.config.get('source', 'realm'),
                                        client_id=self.config.get('source', 'client_id'),
                                        client_secret=self.config.get('source', 'client_secret'),
                                        ca_file=self.config.get('common', 'ca_file'),
                                        next_param=self.config.get('source', 'next_param'))
        if self.config.get('target', 'mode') == "server":
            self.target_client = FHIRClient(base_url=self.config.get('target', 'base_url'),
                                            username=self.config.get('target', 'username'),
                                            password=self.config.get('target', 'password'),
                                            ca_file=self.config.get('common', 'ca_file'))

    def fetch_resources(self, search_path: str) -> None:
        """
        Fetch resources based on the given search path and process them.

        :param search_path: The search path for fetching resources.
        :type search_path: str
        """
        logger.info("Started Processing")
        if "?" in search_path:
            search_resource_type, search_params = search_path.split("?", maxsplit=1)
            params = self.get_request_params(search_resource_type) | self.parse_query_params(search_params)
        else:
            search_resource_type = search_path
            params = self.get_request_params(search_resource_type)
        for bundle in self.source_client.search(search_resource_type, params=params):
            logger.debug(f"Fetched a bundle with {bundle.total} entries.")

            current_bundle = None
            max_bundle_size = self.config.getint('bundling', 'bundle_size')

            for num, entry in enumerate(bundle.entry):
                # Initialize an empty Bundle if needed
                if current_bundle is None:
                    current_bundle = Bundle(
                        type=self.config.get('bundling', 'bundle_type'),
                        entry=[],
                        identifier=Identifier(system="http://www.uniklinikum-jena.de/fhir/sid/consentBundle",
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
                    logger.debug(f"Sending bundle {num} to ouput.")
                    self._handle_bundle_output(current_bundle)
                    current_bundle = None

            # Handle remaining entries after loop
            if current_bundle is not None and len(current_bundle.entry) > 0:
                logger.debug(f"Sending last bundle to ouput.")
                current_bundle = self.post_process_bundle(current_bundle)
                self._handle_bundle_output(current_bundle)

    def _handle_bundle_output(self, bundle: Bundle) -> None:
        """
        Handle the output of a processed bundle.

        :param bundle: The bundle to be handled.
        :type bundle: Bundle
        """
        if self.config.get('target', 'mode') == "file":
            self._write_bundle_to_file(bundle)
        elif self.config.get('target', 'mode') == "server":
            self._send_bundle_to_server(bundle)

    def _write_bundle_to_file(self, bundle: Bundle) -> None:
        """
        Write a bundle to a file.

        :param bundle: The bundle to be written to a file.
        :type bundle: Bundle
        """
        outdir = self.config.get('target', 'outdir')
        if not os.path.exists(outdir):
            os.mkdir(outdir)
        if len(bundle.entry) > 0:
            logger.info(f"Writing bundle with {len(bundle.entry)} entries to file.")
            with open(os.path.join(outdir, f'{bundle.identifier.value}.json'), 'w',
                      encoding='utf-8') as outfile:
                outfile.truncate(0)
                outfile.write(bundle.model_dump_json())
        else:
            logger.warning("Bundle did not contain any resource. This may occur if all resources are filtered. "
                           "No output file written.")

    def _send_bundle_to_server(self, bundle: Bundle) -> None:
        """
       Send a bundle to the target server.

       :param bundle: The bundle to be sent to the server.
       :type bundle: Bundle
       """
        logger.info(f"Sending bundle with {len(bundle.entry)} entries to target server at "
                    f"{self.target_client.base_url}...")
        r = self.target_client.post_bundle(bundle)

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

    @staticmethod
    def post_process_resource(resource: Resource) -> Resource:
        """
        Post-process a single resource.

        :param resource: The resource to be post-processed.
        :type resource: Resource
        :return: The post-processed resource.
        :rtype: Resource
        """
        del resource.id
        del resource.meta.lastUpdated
        return resource

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
            request = self.create_entry_request(resource)
            yield BundleEntry(resource=resource, request=request)

    def process_resource(self, resource: Resource) -> Iterator[Resource]:
        """
        Process a single resource, including handling references and specific resource types.

        :param resource: The resource to be processed.
        :type resource: Resource
        :return: An iterator of processed Resource objects.
        :rtype: Iterator[Resource]
        """
        logger.info(f"Processing resource {resource.get_resource_type()}/{resource.id}")

        if resource.get_resource_type() == 'Consent' and self.contains_category_coding(resource, target_system="http://loinc.org", target_code="59284-0"):
            logger.warning(f"Resource {resource.get_resource_type()}/{resource.id} has a category.coding for "
                           f"http://loinc.org with code '59284-0'. Ignoring this resource!")
            return

        references = self.extract_references(resource)
        for reference in references:
            ignored_references = list(self.config.get('bundling', 'ignore_references').split(','))
            if reference.get('resource_type') not in ignored_references:
                for referenced_resource in self.fetch_reference(reference):
                    for processed_reference_resource in self.process_resource(referenced_resource):
                        if self.config.get('bundling', 'reference_mode') == 'identifier':
                            if reference.get('type') != 'logical' or 'identifier' not in reference.get('params').keys():
                                identifier = self.get_primary_identifier(processed_reference_resource)
                                new_reference = f"{processed_reference_resource.get_resource_type()}?identifier={identifier.system}|{identifier.value}"
                                resource = self.update_reference(resource, reference.get('path'), new_reference)
                        # TODO: rewrite reference modes id and fullUrl
                        yield processed_reference_resource

            else:
                logger.info(f"Reference of type {reference.get('resource_type')} ignored.")

        if resource.get_resource_type() == 'Consent':
            pat_id = resource.patient.display.split(' ', 1)[1]
            resource.patient.reference = f"Patient?identifier=http://www.uniklinikum-jena.de/fhir/sid/patient|{pat_id}"
            # Create a static identifier
            organization = resource.organization[0].display
            policy = resource.policy[0].uri
            identifier_value = hashlib.sha256(str.encode(f"{organization}+{policy}+{pat_id}")).hexdigest()
            resource = self.add_identifier(resource,
                                           identifier_system="http://www.uniklinikum-jena.de/fhir/sid/consent",
                                           identifier_value=identifier_value)
            del resource.patient.display

        if resource.get_resource_type() == 'ResearchStudy':
            resource = self.add_identifier(resource,
                                           identifier_system="http://www.uniklinikum-jena.de/fhir/sid/consentSource",
                                           identifier_value=resource.id)
            resource.meta.profile[0] = "http://fhir.de/ConsentManagement/StructureDefinition/Domain/ResearchStudy"

        # All resources
        resource = self.add_source(resource, "http://www.uniklinikum-jena.de/fhir/origin/gics")

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
    def contains_category_coding(resource: Resource, target_system: str, target_code: str) -> bool:
        """
        Checks if a FHIR resource contains a specific coding in its category.

        :param resource: FHIR resource object (Patient, Observation, etc.).
        :type resource: Resource
        :param target_system: The coding system URI to match.
        :type target_system: str
        :param target_code: The code value to match.
        :type target_code: str
        :return: True if matching coding is found in category.
        :rtype: bool
        """
        return any(
            any(coding.system == target_system and coding.code == target_code
                for coding in category.coding or [])
            for category in getattr(resource, 'category', [])
        )

    @staticmethod
    def add_identifier(resource: Resource, identifier_system: str, identifier_value: str) -> Resource:
        """
        Add an identifier to a FHIR resource.

        :param resource: FHIR resource to add identifier to.
        :type resource: Resource
        :param identifier_system: The system for the identifier.
        :type identifier_system: str
        :param identifier_value: The value for the identifier.
        :type identifier_value: str
        :return: Updated FHIR resource with new identifier.
        :rtype: Resource
        """
        identifier = Identifier(system=identifier_system, value=identifier_value)

        identifier_field = resource.model_fields.get('identifier')
        if identifier_field:
            if 'List' in str(identifier_field.annotation):
                resource.identifier = [identifier]
            elif 'Identifier' in str(identifier_field.annotation):
                resource.identifier = identifier
            else:
                raise ValueError("Unknown identifier field data type in this resource type.")
        else:
            raise ValueError("No identifier field in this resource type.")

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
        """
        identifier_field = resource.model_fields.get('identifier')
        if identifier_field:
            if 'List' in str(identifier_field.annotation):
                if system is not None:
                    for identifier in resource.identifier:
                        if identifier.system == system:
                            return identifier
                        logger.warning(
                            f"Identifier system {system} was not found in resource {resource.id}. Falling back "
                            f"to first identifier entry.")
                return resource.identifier[0]
            elif 'Identifier' in str(identifier_field.annotation):
                if system is not None:
                    logger.warning(f"Resource type {resource.get_resource_type()} only supports single identifiers. "
                                   f"Matching of identifier system ignored.")
                return resource.identifier
            else:
                raise ValueError("Unkown identifier field data type in this resource type.")

    @staticmethod
    def add_source(resource: Resource, source: str) -> Resource:
        """
        Add a source to a FHIR resource's metadata.

        :param resource: FHIR resource to add source to.
        :type resource: Resource
        :param source: The source to add.
        :type source: str
        :return: Updated FHIR resource with new source.
        :rtype: Resource
        """
        resource.meta.source = source
        return resource

    def create_entry_request(self, resource: Resource) -> BundleEntryRequest:
        """
        Create a BundleEntryRequest for a given resource.

        :param resource: The resource to create a request for.
        :type resource: Resource
        :return: A BundleEntryRequest for the given resource.
        :rtype: BundleEntryRequest
        """
        request_method = (self.config.get('bundling', f'request_method_{resource.get_resource_type()}', fallback=None) or
                          self.config.get('bundling', f'request_method', fallback=None))
        if ':' in request_method:
            request_method, request_condition = request_method.split(':', 1)
        else:
            request_condition = None

        request_url = (self.config.get('bundling', f'request_url_{resource.get_resource_type()}', fallback=None) or
                       self.config.get('bundling', f'request_url', fallback=None))
        # Default to id mode
        url = f"{resource.get_resource_type()}/{resource.id}"
        if request_method:
            if request_url == 'identifier':
                identifier = self.get_primary_identifier(resource)
                url = f"{resource.get_resource_type()}?identifier={identifier.system}|{identifier.value}"
            elif request_url == 'id':
                url = f"{resource.get_resource_type()}/{resource.id}"
            # TODO: fullUrl still needs work
        else:
            # Fallback to PUT
            request_method = "PUT"

        request = BundleEntryRequest(
            method=request_method.upper(),
            url=url if request_condition is None else resource.get_resource_type(),
            ifNoneExist=url if request_condition == 'ifNoneExist' else None,
            #TODO: Not sure about these
            #ifNoneMatch = url if request_condition == 'ifNoneMatch' else None,
            #ifModifiedSince = url if request_condition == 'ifModifiedSince' else None,
            #ifMatch = url if request_condition == 'ifMatch' else None,
        )

        return request

    def get_request_params(self, resource_type: str) -> Dict[str, Any]:
        """
        Get request parameters for a given resource type.

        :param resource_type: The type of resource to get parameters for.
        :type resource_type: str
        :return: A dictionary of request parameters.
        :rtype: Dict[str, Any]
        """
        request_params = (self.config.get('source', f'request_params_{resource_type}', fallback=None) or
                          self.config.get('source', 'request_params', fallback=None))
        params = {}
        if request_params:
            params = self.parse_query_params(request_params)
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


