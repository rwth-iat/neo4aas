import logging
import os
from pathlib import Path
from typing import Tuple

from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer
from basyx.aas.model import AbstractObjectStore
from basyx.aas.model.provider import DictIdentifiableStore as _FileStore

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG
from aas_mapping.aas_neo4j_adapter.neo_aas_object_store import Neo4jObjectStore


def build_neo4j_object_store(uri: str, user: str, password: str) -> Neo4jObjectStore:
    # Repair non-conformant imported data (e.g. BCP 47 language tags) by default; set
    # FIX_ON_IMPORT=false to disable.
    fix_on_import = os.getenv("FIX_ON_IMPORT", "true").lower() not in ("0", "false", "no")
    client = AASNeo4JClient(
        uri=uri, user=user, password=password,
        model_config=AAS_NEO4J_MODEL_CONFIG, fix_on_import=fix_on_import,
    )
    return Neo4jObjectStore(client=client)


_AAS_SUFFIXES = (".json", ".xml", ".aasx")


def _select_input_files(env_input: str, logger: logging.Logger) -> list:
    """Collect AAS files under INPUT, capping per immediate subdirectory.

    Top-level files form one group; each immediate subdirectory (recursively) forms
    its own group. With MAX_PER_DIR>0 each group is capped to that many files (e.g. a
    "max N AAS per manufacturer" supplier repo where each manufacturer is a subdir).
    MAX_PER_DIR=0 (default) loads everything, so the flat-directory demonstrator is
    unchanged. Sorting makes the selection deterministic.
    """
    max_per_dir = int(os.getenv("MAX_PER_DIR", "0"))
    root = Path(env_input)
    groups: dict[str, list] = {}
    for entry in sorted(root.iterdir()):
        if entry.is_file() and entry.suffix.lower() in _AAS_SUFFIXES:
            groups.setdefault("", []).append(entry)
        elif entry.is_dir():
            groups[entry.name] = sorted(
                p for p in entry.rglob("*")
                if p.is_file() and p.suffix.lower() in _AAS_SUFFIXES
            )

    selected: list = []
    for name, files in groups.items():
        chosen = files[:max_per_dir] if max_per_dir > 0 else files
        selected.extend(chosen)
        if name:
            logger.info(
                'Group "%s": selected %d of %d AAS file(s)', name, len(chosen), len(files)
            )
    logger.info("Selected %d AAS file(s) total (MAX_PER_DIR=%d)", len(selected), max_per_dir)
    return selected


def build_neo4j_storage(
    env_input: str, logger: logging.Logger
) -> Tuple[AbstractObjectStore, DictSupplementaryFileContainer]:
    neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD", "")
    logger.info('Using Neo4j backend at "%s" (user=%s)', neo4j_uri, neo4j_user)
    store = build_neo4j_object_store(neo4j_uri, neo4j_user, neo4j_password)

    if not os.path.isdir(env_input):
        logger.warning('INPUT directory "%s" not found, starting empty Neo4j store', env_input)
        return store, DictSupplementaryFileContainer()

    # Ingest via the neo4aas client's own importers (upload_json_file/upload_xml_file), NOT
    # basyx's read_aas_*_file_into. basyx's strict reader validates at parse time and rejects
    # real-world supplier data (BCP 47 language tags, numeric valueType holding a string,
    # AASd-* constraint violations), dropping elements or whole files. The client maps the
    # raw JSON/XML dict straight to Neo4j and runs apply_fixers() inside _process_json_data
    # (so XML imports get the same repair via MRO), preserving non-conformant content. The
    # Repository still serves the result through this Neo4jObjectStore.
    client = store._client
    input_supp_files = DictSupplementaryFileContainer()
    loaded = failed = 0
    for file in _select_input_files(env_input, logger):
        suffix = file.suffix.lower()
        try:
            if suffix == ".json":
                client.upload_json_file(str(file))
            elif suffix == ".xml":
                client.upload_xml_file(str(file))
            elif suffix == ".aasx":
                # .aasx carries supplementary files, so still go through the basyx reader;
                # add_identifiable applies the same fixers on this single-object path.
                file_store = _FileStore()
                with AASXReader(file) as reader:
                    reader.read_into(object_store=file_store, file_store=input_supp_files)
                for obj in file_store:
                    store.add(obj)
            else:
                continue
            loaded += 1
        except Exception as exc:
            # Source AAS files are external data: a single malformed file must not abort the
            # whole bulk load, so skip it and keep going.
            failed += 1
            logger.warning('Skipping unloadable AAS file "%s": %s', file.name, exc)

    # Materialize :references edges once over the whole graph (semanticId/submodel/etc.),
    # rather than incrementally per object as store.add would — far cheaper for a bulk load.
    edges = client.resolve_references()
    logger.info(
        'Loaded %d AAS file(s) from "%s" into Neo4j (%d skipped on error); resolved %d reference edge(s)',
        loaded, env_input, failed, edges,
    )
    return store, input_supp_files
