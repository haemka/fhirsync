"""Where the bundler gets resources from: a FHIR server or FHIR files.

Both sources offer the same three operations: the results of a run as pages (lists of resources), reading a
referenced resource by type and id, and searching for a reference written as Type?<parameters>.
"""
import logging
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests
from dic_helpers.directory_reader import DirectoryReader
from dic_helpers.fhir_client import FHIRClient
from dic_helpers.fhir_file_reader import FHIRFileReader
from fhir.resources.R4B.resource import Resource

from fhirsync.config import Source

logger = logging.getLogger(__name__)


class ServerSource:
    """A FHIR server, read with dic-helper-toolkit's FHIRClient."""

    def __init__(self, client: FHIRClient) -> None:
        """
        Initialize the source with its client.

        :param client: The client of the source server.
        :type client: FHIRClient
        """
        self.client = client

    def pages(self, resource_type: str, params: Dict[str, Any]) -> Iterator[List[Resource]]:
        """
        Run a search and return its result pages.

        :param resource_type: The resource type to search for.
        :type resource_type: str
        :param params: The search parameters.
        :type params: Dict[str, Any]
        :return: The resources of every result page, a page without entries as an empty list.
        :rtype: Iterator[List[Resource]]
        """
        for bundle in self.client.search(resource_type, params=params):
            logger.debug(f"Fetched a bundle with {bundle.total} entries.")
            yield [entry.resource for entry in bundle.entry or [] if entry.resource is not None]

    def read(self, resource_type: str, resource_id: str) -> Optional[Resource]:
        """
        Read a referenced resource.

        :param resource_type: The resource type.
        :type resource_type: str
        :param resource_id: The id on the server.
        :type resource_id: str
        :return: The resource, None if the server answers 404.
        :rtype: Optional[Resource]
        :raises requests.HTTPError: For other HTTP errors.
        """
        try:
            return self.client.get_resource(resource_type, resource_id)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise

    def search(self, resource_type: str, params: Dict[str, Any]) -> List[Any]:
        """
        Search for a reference written as Type?<parameters>.

        Known limitation (docs/rules.md section 8): the result pages are returned as they are, so the bundler processes
        the search result Bundles instead of the found resources.

        :param resource_type: The resource type to search for.
        :type resource_type: str
        :param params: The search parameters.
        :type params: Dict[str, Any]
        :return: The result pages, an empty list if no page has an entry.
        :rtype: List[Any]
        """
        pages = list(self.client.search(resource_type, params=params))
        return pages if any(page.entry for page in pages) else []


class FileSource:
    """
    FHIR files, listed with dic-helper-toolkit's DirectoryReader and read with its FHIRFileReader. All files are read
    when the source is created, and every resource with an id is kept in an index by type and id for references.
    """

    def __init__(self, settings: Source) -> None:
        """
        Read all files of the source.

        :param settings: The source section of the configuration, with type files.
        :type settings: Source
        :raises FileNotFoundError: If the directory doesn't exist.
        :raises NotADirectoryError: If the directory is a file.
        :raises dic_helpers.fhir_file_reader.FHIRFileError: For an invalid file, line or resource with on_error raise.
        """
        self.resource_types = settings.resource_types
        paths = DirectoryReader(settings.directory, patterns=settings.patterns, recursive=settings.recursive,
                                include_hidden=settings.include_hidden,
                                follow_symlinks=settings.follow_symlinks).list_files()
        logger.info(f"Reading {len(paths)} files from {settings.directory}")
        self.items = list(FHIRFileReader(paths, unpack_bundles=settings.unpack_bundles,
                                         on_error=settings.on_error).resources())
        self.index: Dict[Tuple[str, str], Resource] = {}
        for item in self.items:
            if item.resource.id is None:
                continue
            key = (item.resource.get_resource_type(), item.resource.id)
            if key in self.index:
                logger.warning(f"{item.path}: {key[0]}/{key[1]} was already read from another file or line, the "
                               f"first one is used for references")
                continue
            self.index[key] = item.resource

    def pages(self) -> Iterator[List[Resource]]:
        """
        The results of the files: one page per file, with the resources of the configured resource_types (all if
        none are configured).

        :return: The resources of every file, in the order of the files and within each file.
        :rtype: Iterator[List[Resource]]
        """
        page: List[Resource] = []
        current_path = None
        for item in self.items:
            if item.path != current_path and page:
                yield page
                page = []
            current_path = item.path
            if not self.resource_types or item.resource.get_resource_type() in self.resource_types:
                page.append(item.resource)
        if page:
            yield page

    def read(self, resource_type: str, resource_id: str) -> Optional[Resource]:
        """
        A referenced resource from the files.

        :param resource_type: The resource type.
        :type resource_type: str
        :param resource_id: The id in the files.
        :type resource_id: str
        :return: The resource, None if the files don't contain it.
        :rtype: Optional[Resource]
        """
        return self.index.get((resource_type, resource_id))

    def search(self, resource_type: str, params: Dict[str, Any]) -> List[Any]:
        """
        References written as Type?<parameters> are not supported for files: nothing is found.

        :param resource_type: The resource type of the reference.
        :type resource_type: str
        :param params: The search parameters of the reference.
        :type params: Dict[str, Any]
        :return: Always an empty list.
        :rtype: List[Any]
        """
        return []
