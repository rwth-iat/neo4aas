"""AASX importer for AAS data.

AASX is a ZIP-based (OPC) package format that bundles AAS XML files together
with associated resources (thumbnails, documents, etc.).

AasxToNeo4jImporter uses composition: it wraps any XmlToNeo4jImporter instance
(including AASNeo4JClient) and delegates import work to it after extracting the
AAS XML files from the AASX archive.

Detection strategy: scan all .xml entries in the ZIP and accept those whose root
element is the AAS 3.0 <environment> tag. This is simpler than OPC relationship
parsing and sufficient for all AAS 3.0-compliant AASX files.
"""
import logging
import os
import time
import xml.etree.ElementTree as ET
import zipfile
from os.path import isfile, join
from typing import Iterator

from neo4aas.core.utils import UploadStats
from neo4aas.core.serialization.xml.importer import XmlToNeo4jImporter
from neo4aas.core.serialization.xml.xml_to_json import AAS_NS, xml_to_aas_json

logger = logging.getLogger(__name__)

_AAS_ENV_TAG = f"{{{AAS_NS}}}environment"


class AasxToNeo4jImporter:
    """Imports AASX (ZIP-based AAS) files into Neo4j.

    Uses composition: wraps any XmlToNeo4jImporter (or subclass) and delegates
    the actual graph import to it after extracting AAS XML from the AASX archive.

    Usage::

        from neo4aas.core.client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG
        from neo4aas.core.serialization.aasx import AasxToNeo4jImporter

        client = AASNeo4JClient(uri=..., user=..., password=..., model_config=AAS_NEO4J_MODEL_CONFIG)
        aasx = AasxToNeo4jImporter(client)
        aasx.upload_aasx_file("path/to/file.aasx")
    """

    def __init__(self, xml_importer: XmlToNeo4jImporter):
        self.xml_importer = xml_importer

    def _iter_aas_xml_bytes(self, aasx_path: str) -> Iterator[bytes]:
        """Yield the raw bytes of each AAS XML environment found in the AASX ZIP."""
        with zipfile.ZipFile(aasx_path, 'r') as zf:
            for name in zf.namelist():
                if not name.endswith('.xml'):
                    continue
                content = zf.read(name)
                try:
                    root = ET.fromstring(content)
                    if root.tag == _AAS_ENV_TAG:
                        yield content
                except ET.ParseError:
                    logger.warning(f"Skipping malformed XML entry '{name}' in {aasx_path}")

    def upload_aasx_file(self, aasx_path: str, db_batch_size: int = 1000) -> UploadStats:
        """Extract AAS XML files from an AASX package and upload them to Neo4j."""
        stats = UploadStats()
        for xml_bytes in self._iter_aas_xml_bytes(aasx_path):
            xml_dict = xml_to_aas_json(xml_bytes)
            file_stats = self.xml_importer.upload_xml(xml_dict, db_batch_size=db_batch_size)
            stats.total_nodes_created += file_stats.total_nodes_created
            stats.total_relationships_created += file_stats.total_relationships_created
            stats.total_node_creation_time += file_stats.total_node_creation_time
            stats.total_relationship_creation_time += file_stats.total_relationship_creation_time
        stats.finish()
        return stats

    def upload_all_aasx_from_dir(self, directory: str, db_batch_size: int = 1000) -> UploadStats:
        """Upload all .aasx files from a directory into Neo4j."""
        stats = UploadStats()
        aasx_files = sorted(
            f for f in os.listdir(directory)
            if f.endswith('.aasx') and isfile(join(directory, f))
        )
        logger.info(f"Found {len(aasx_files)} AASX files in '{directory}'")

        for fname in aasx_files:
            logger.info(f"Uploading {fname}")
            start = time.time()
            file_stats = self.upload_aasx_file(join(directory, fname), db_batch_size=db_batch_size)
            elapsed = time.time() - start
            logger.info(
                f"  → {file_stats.total_nodes_created} nodes, "
                f"{file_stats.total_relationships_created} rels in {elapsed:.2f}s"
            )
            stats.total_files += 1
            stats.total_nodes_created += file_stats.total_nodes_created
            stats.total_relationships_created += file_stats.total_relationships_created
            stats.total_node_creation_time += file_stats.total_node_creation_time
            stats.total_relationship_creation_time += file_stats.total_relationship_creation_time

        stats.finish()
        return stats
