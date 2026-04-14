import logging
from typing import Dict, Optional

from aas_mapping.aas_neo4j_adapter.aas_neo4j_importer import (
    AASNeo4jImporter,
    AAS_NEO4J_MODEL_CONFIG,
    IDENTIFIABLE_KEYS,  # re-exported: imported by test_roundtrip.py
)
from aas_mapping.aas_neo4j_adapter.jsonification.neo4j_export import JsonFromNeo4jExporter

# Configure logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


class AASNeo4JClient(AASNeo4jImporter):
    """Thin facade combining AAS import (AASNeo4jImporter) and export (JsonFromNeo4jExporter).

    The exporter shares the driver already opened by the importer — no second connection.
    All import, CRUD, and count methods are inherited from AASNeo4jImporter.
    """

    def __init__(self, uri: str, user: str, password: Optional[str] = None,
                 model_config=AAS_NEO4J_MODEL_CONFIG):
        super().__init__(uri, user, password, model_config)
        self._exporter = JsonFromNeo4jExporter.from_driver(self.driver, self.model_config)

    def get_referable(self, parent_id: str, id_short_path: str = None) -> Dict:
        subgraph_json = self._get_subgraph_of_referable(parent_id, id_short_path)
        return self._strip_internal_keys(self._exporter.convert_subgraph_to_data_dict(subgraph_json))

    def get_identifiable(self, identifier: str) -> Dict:
        return self.get_referable(identifier)


def main():
    def optimized_upload_all_submodels(submodels_folder="../examples/aas/test_dataset/"):
        optimized_client = AASNeo4JClient(uri="bolt://localhost:7687", user="neo4j", password="12345678",
                                          model_config=AAS_NEO4J_MODEL_CONFIG)
        optimized_client._remove_all()
        optimized_client._remove_all_indexes_and_constraints()
        optimized_client.optimize_database()
        import time
        start_time = time.time()
        result = optimized_client.upload_all_json_from_dir(submodels_folder,
                                                           file_batch_size=100,
                                                           db_batch_size=30000,
                                                           max_num_of_batches=1)
        end_time = time.time()
        print(f"Execution time: {end_time - start_time} seconds")

    def get_sm_from_neo4j():
        client = AASNeo4JClient(uri="bolt://localhost:7687", user="neo4j", password="12345678")
        sm = client.get_identifiable('https://smart.festo.com/sm/004/2dcd48b2-88a5-463a-9396-deaece98b4c9/')
        print(sm)

    logger.setLevel(logging.INFO)
    optimized_upload_all_submodels()


if __name__ == '__main__':
    main()
