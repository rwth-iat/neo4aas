import logging
import os
from pathlib import Path
from typing import Tuple

from basyx.aas.adapter.aasx import AASXReader, DictSupplementaryFileContainer
from basyx.aas.model import AbstractObjectStore
from basyx.aas.model.provider import DictIdentifiableStore as _FileStore

from neo4aas.core.client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG
from neo4aas.core.io import aas_suffix, is_aas_file, list_aas_files
from neo4aas.core.serialization.aasx import AasxToNeo4jImporter
from neo4aas.basyx_ext.object_store import Neo4jObjectStore


def _read_supplementary_files(path, container: DictSupplementaryFileContainer,
                              logger: logging.Logger) -> None:
    """Best-effort extraction of an AASX package's supplementary files (thumbnails, PDFs).

    Only basyx can populate its own file container, and its reader rejects packages
    whose metamodel namespace it does not know — so a failure here must not fail the
    import: the AAS content is already loaded by neo4aas' own package reader.
    """
    try:
        with AASXReader(str(path)) as reader:
            reader.read_into(object_store=_FileStore(), file_store=container)
    except Exception as exc:  # noqa: BLE001 — supplementary files are optional
        logger.info('No supplementary files read from "%s": %s', getattr(path, "name", path), exc)


def build_neo4j_object_store(uri: str, user: str, password: str) -> Neo4jObjectStore:
    # Repair non-conformant imported data (e.g. BCP 47 language tags) by default; set
    # FIX_ON_IMPORT=false to disable.
    fix_on_import = os.getenv("FIX_ON_IMPORT", "true").lower() not in ("0", "false", "no")
    client = AASNeo4JClient(
        uri=uri, user=user, password=password,
        model_config=AAS_NEO4J_MODEL_CONFIG, fix_on_import=fix_on_import,
    )
    return Neo4jObjectStore(client=client)


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
        # is_aas_file/aas_suffix ignore a trailing `.gz`: real corpora ship instances
        # compressed (`x.json.gz`), and a suffix-only match found none of them.
        if entry.is_file() and is_aas_file(entry):
            groups.setdefault("", []).append(entry)
        elif entry.is_dir():
            groups[entry.name] = list_aas_files(entry, recursive=True)

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
    aasx_importer = AasxToNeo4jImporter(client)
    input_supp_files = DictSupplementaryFileContainer()
    loaded = failed = 0
    for file in _select_input_files(env_input, logger):
        suffix = aas_suffix(file)
        try:
            if suffix == ".json":
                client.upload_json_file(str(file))
            elif suffix == ".xml":
                client.upload_xml_file(str(file))
            elif suffix == ".aasx":
                # Load the AAS content with neo4aas' own package reader, for the same
                # reason the JSON/XML paths avoid basyx: basyx's AASXReader accepts only
                # the V3.0 namespace and *does not raise* when it recognizes nothing — a
                # V3.1 package (e.g. the in-house Pumpwerk data) read as zero objects and
                # was counted as loaded. Supplementary files still come from the basyx
                # reader, best effort, since they are outside neo4aas' scope.
                for env in aasx_importer.iter_environments(str(file)):
                    client.upload_json(env)
                _read_supplementary_files(file, input_supp_files, logger)
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
