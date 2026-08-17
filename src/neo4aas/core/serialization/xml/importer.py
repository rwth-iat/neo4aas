"""XML-to-Neo4j importer for AAS data.

XmlToNeo4jImporter extends JsonToNeo4jImporter by adding XML upload methods.
The XML is first converted to an AAS JSON-equivalent dict via xml_to_json,
then the entire existing JSON import pipeline (_process_json_data, _process_dict,
deduplication, batch upload) is reused unchanged.
"""
import logging
import os
import time
from os.path import isfile, join
from typing import Optional

from neo4aas.core.base import Neo4jModelConfig
from neo4aas.core.serialization.json.importer import JsonToNeo4jImporter
from neo4aas.core.utils import UploadStats
from neo4aas.core.serialization.xml.xml_to_json import xml_to_aas_json

logger = logging.getLogger(__name__)


class XmlToNeo4jImporter(JsonToNeo4jImporter):
    """Imports AAS XML files into Neo4j, reusing the JSON import pipeline.

    XML is converted to an AAS JSON-equivalent dict via xml_to_aas_json(), then
    delegated to _process_json_data() — the same entry point used by upload_json().
    Subclass overrides of _process_json_data() (e.g. in AASNeo4JClient) therefore
    apply to XML imports automatically via Python MRO.
    """

    def upload_xml(self, xml_dict: dict, db_batch_size: int = 1000) -> UploadStats:
        """Upload a pre-parsed AAS XML dict (same structure as AAS JSON) to Neo4j."""
        nodes, relationships = self._process_json_data(xml_dict)
        stats = self._upload_nodes_and_relationships(nodes, relationships, db_batch_size=db_batch_size)
        stats.finish()
        return stats

    def upload_xml_file(self, file_path: str, db_batch_size: int = 1000) -> UploadStats:
        """Parse an AAS XML file and upload its contents to Neo4j."""
        xml_dict = xml_to_aas_json(file_path)
        return self.upload_xml(xml_dict, db_batch_size=db_batch_size)

    def upload_all_xml_from_dir(self, directory: str, file_batch_size: int = 50,
                                db_batch_size: int = 10000, max_num_of_batches: int = 10000) -> UploadStats:
        """Batch-upload all .xml files from a directory into Neo4j.

        Mirrors upload_all_json_from_dir: files are grouped into batches of
        file_batch_size, processed into nodes/relationships, then written to
        Neo4j in db_batch_size-sized transactions.
        """
        stats = UploadStats()
        xml_files = [f for f in os.listdir(directory) if isfile(join(directory, f)) and f.endswith('.xml')]
        stats.total_files = len(xml_files)
        stats.total_batches = (stats.total_files + file_batch_size - 1) // file_batch_size

        logger.info(f"Found {stats.total_files} XML files in directory '{directory}'")
        logger.info(f"File batch size: {stats.total_batches} batches of {file_batch_size} files each")
        logger.info(f"Database transaction batch size: {db_batch_size}")

        for batch_num in range(stats.total_batches):
            start_idx = batch_num * file_batch_size
            end_idx = min(start_idx + file_batch_size, stats.total_files)
            current_file_batch = xml_files[start_idx:end_idx]

            logger.info(f"\n--- Processing Batch {batch_num + 1}/{stats.total_batches} ---")
            logger.info(f"Files {start_idx + 1}-{end_idx} of {stats.total_files}")

            batch_start_time = time.time()
            batch_nodes = []
            batch_relationships = {}

            for filename in current_file_batch:
                xml_dict = xml_to_aas_json(join(directory, filename))
                nodes, relationships = self._process_json_data(xml_dict)
                batch_nodes.extend(nodes)
                self._merge_relationships(batch_relationships, relationships)

            processing_time = time.time() - batch_start_time
            stats.total_processing_time += processing_time
            logger.info(f"Processed {len(current_file_batch)} files in {processing_time:.2f} seconds")

            if not batch_nodes:
                logger.info("No nodes to create in this batch, skipping...")
                continue

            self._upload_nodes_and_relationships(batch_nodes, batch_relationships, stats,
                                                 db_batch_size=db_batch_size)

            batch_total_time = time.time() - batch_start_time
            logger.info(f"Batch {batch_num + 1} completed in {batch_total_time:.2f} seconds")

            if batch_num == max_num_of_batches:
                logger.warning("Max number of batches reached")
                break

        stats.finish()
        return stats
