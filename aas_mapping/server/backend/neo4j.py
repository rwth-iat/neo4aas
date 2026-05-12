import logging
import os
import time
from pathlib import Path
from typing import Tuple

from basyx.aas.adapter import read_aas_json_file_into, read_aas_xml_file_into
from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer
from basyx.aas.model import AbstractObjectStore
from basyx.aas.model.provider import DictIdentifiableStore as _FileStore

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG
from aas_mapping.aas_neo4j_adapter.neo_aas_object_store import Neo4jObjectStore


def build_neo4j_object_store(uri: str, user: str, password: str) -> Neo4jObjectStore:
    client = AASNeo4JClient(uri=uri, user=user, password=password, model_config=AAS_NEO4J_MODEL_CONFIG)
    return Neo4jObjectStore(client=client)


def build_neo4j_storage(
    env_input: str, logger: logging.Logger
) -> Tuple[AbstractObjectStore, DictSupplementaryFileContainer]:
    from neo4j.exceptions import ServiceUnavailable

    neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD", "")
    logger.info('Using Neo4j backend at "%s" (user=%s)', neo4j_uri, neo4j_user)
    store = build_neo4j_object_store(neo4j_uri, neo4j_user, neo4j_password)

    if not os.path.isdir(env_input):
        logger.warning('INPUT directory "%s" not found, starting empty Neo4j store', env_input)
        return store, DictSupplementaryFileContainer()

    input_supp_files = DictSupplementaryFileContainer()
    input_objects: list = []
    for file in Path(env_input).iterdir():
        if not file.is_file():
            continue
        file_store = _FileStore()
        suffix = file.suffix.lower()
        if suffix == ".json":
            with open(file) as f:
                read_aas_json_file_into(file_store, f)
        elif suffix == ".xml":
            with open(file) as f:
                read_aas_xml_file_into(file_store, f)
        elif suffix == ".aasx":
            with AASXReader(file) as reader:
                reader.read_into(object_store=file_store, file_store=input_supp_files)
        else:
            continue
        input_objects.extend(file_store)

    loaded, skipped = 0, 0
    for attempt in range(10):
        try:
            for obj in input_objects:
                try:
                    store.add(obj)
                    loaded += 1
                except KeyError:
                    skipped += 1
            break
        except ServiceUnavailable as exc:
            if attempt == 9:
                raise
            logger.warning("Neo4j not reachable (%s), retrying in 3s (%d/9)...", exc, attempt + 1)
            loaded, skipped = 0, 0
            time.sleep(3)

    logger.info(
        'Loaded %d identifiable(s) from "%s" into Neo4j (%d skipped, already existed)',
        loaded, env_input, skipped,
    )
    return store, input_supp_files
