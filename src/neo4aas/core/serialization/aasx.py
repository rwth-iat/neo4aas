"""AASX importer for AAS data.

AASX is a ZIP-based (OPC) package format that bundles an AAS environment together with
associated resources (thumbnails, documents, ...). The environment part is XML **or**
JSON — both are used in the wild — and it carries whichever metamodel namespace the
producing tool wrote (``3/0``, ``3/1``, ...).

AasxToNeo4jImporter uses composition: it wraps any XmlToNeo4jImporter instance
(including AASNeo4JClient) and delegates import work to it after extracting the AAS
environments from the AASX archive.

Detection strategy: scan the package's entries and accept

* ``.xml`` parts whose root element is an ``environment`` in any
  ``https://admin-shell.io/aas/...`` namespace, and
* ``.json`` parts that hold at least one top-level Identifiable list.

This is simpler than OPC relationship parsing and — unlike a scan pinned to one
namespace or one encoding — actually finds the environment in real vendor packages: in
the reference corpus one supplier's packages store the environment as ``aasx/data.json``
and the in-house packages declare the AAS **V3.1** namespace. Both used to yield *no* AAS
part and import silently nothing. A package that yields nothing is now logged as a
warning, and a package whose ZIP central directory is missing (truncated download) is
still walked entry by entry — the AAS part is written before the bulky supplementary
files, so it survives the cut.
"""
import io
import json
import logging
import os
import struct
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from typing import Any, Dict, Iterator

from neo4aas.core.io import list_aas_files, read_bytes
from neo4aas.core.utils import UploadStats
from neo4aas.core.serialization.xml.importer import XmlToNeo4jImporter
from neo4aas.core.serialization.xml.xml_to_json import xml_to_aas_json

logger = logging.getLogger(__name__)

#: Namespace prefix shared by every AAS metamodel version (3/0, 3/1, ...).
_AAS_NS_PREFIX = "https://admin-shell.io/aas/"

#: Top-level keys that mark a JSON part as an AAS environment.
_ENV_KEYS = ("assetAdministrationShells", "submodels", "conceptDescriptions")

#: OPC bookkeeping parts, never AAS content.
_OPC_PARTS = ("_rels/", "[content_types].xml", "docprops/")


def _is_aas_environment_xml(root: ET.Element) -> bool:
    """True for an ``<environment>`` root in any AAS metamodel namespace."""
    tag = root.tag
    if not tag.startswith("{"):
        return tag == "environment"
    namespace, _, local = tag[1:].partition("}")
    return local == "environment" and namespace.startswith(_AAS_NS_PREFIX)


def _is_opc_part(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in _OPC_PARTS)


def _iter_local_entries(raw: bytes) -> Iterator[tuple[str, bytes]]:
    """Yield ``(name, content)`` by walking a ZIP's **local file headers**.

    Used when the central directory is missing — a package truncated mid-download still
    holds its earlier parts, and in AASX the environment is written first (the tail is
    thumbnails and PDFs). ``zipfile`` reads the central directory only, so it rejects such
    a file wholesale; this walk recovers whatever is intact and stops at the cut.
    """
    offset = 0
    while raw[offset:offset + 4] == b"PK\x03\x04":
        header = raw[offset + 4:offset + 30]
        if len(header) < 26:
            return
        flags, method, _, _, _, csize, _, name_len, extra_len = struct.unpack(
            "<HHHHIIIHH", header[2:]
        )
        name = raw[offset + 30:offset + 30 + name_len].decode("utf-8", "replace")
        data_at = offset + 30 + name_len + extra_len
        if flags & 0x08 and not csize:
            # Streamed entry: the size lives in a trailing data descriptor, so the only way
            # to find the next header is to inflate this one.
            decompressor = zlib.decompressobj(-15)
            try:
                content = decompressor.decompress(raw[data_at:])
            except zlib.error:
                return
            consumed = len(raw) - data_at - len(decompressor.unused_data)
            if not decompressor.eof:
                return  # cut mid-entry: nothing complete left to read
            yield name, content
            offset = data_at + consumed + 16  # 16 = data descriptor (with signature)
            continue
        chunk = raw[data_at:data_at + csize]
        if len(chunk) < csize:
            return  # cut mid-entry
        try:
            yield name, zlib.decompress(chunk, -15) if method == 8 else chunk
        except zlib.error:
            return
        offset = data_at + csize


class AasxToNeo4jImporter:
    """Imports AASX (ZIP-based AAS) files into Neo4j.

    Uses composition: wraps any XmlToNeo4jImporter (or subclass) and delegates
    the actual graph import to it after extracting the AAS environments.

    Usage::

        from neo4aas.core.client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG
        from neo4aas.core.serialization.aasx import AasxToNeo4jImporter

        client = AASNeo4JClient(uri=..., user=..., password=..., model_config=AAS_NEO4J_MODEL_CONFIG)
        aasx = AasxToNeo4jImporter(client)
        aasx.upload_aasx_file("path/to/file.aasx")
    """

    def __init__(self, xml_importer: XmlToNeo4jImporter):
        self.xml_importer = xml_importer

    def iter_environments(self, source: str | os.PathLike | io.IOBase) -> Iterator[Dict[str, Any]]:
        """Yield one AAS-JSON environment dict per AAS part in the package.

        ``source`` is a path (gzipped or not) or an open binary stream. A package whose
        central directory is missing (truncated download) is still walked entry by entry,
        so its AAS part — written before the bulky supplementary files — is recovered.
        ``zipfile.BadZipFile`` is raised only when nothing at all can be read, so a
        corrupt file never passes as an empty package.
        """
        if isinstance(source, (str, os.PathLike)):
            raw = read_bytes(source)
            label = str(source)
        else:
            raw = source.read()
            label = "<stream>"

        found = 0
        for name, content in self._iter_parts(raw, label):
            if _is_opc_part(name):
                continue
            lowered = name.lower()
            if lowered.endswith(".xml"):
                try:
                    root = ET.fromstring(content)
                except ET.ParseError:
                    logger.warning("Skipping malformed XML entry '%s' in %s", name, label)
                    continue
                if _is_aas_environment_xml(root):
                    found += 1
                    yield xml_to_aas_json(content)
            elif lowered.endswith(".json"):
                try:
                    env = json.loads(content)
                except ValueError:
                    logger.warning("Skipping malformed JSON entry '%s' in %s", name, label)
                    continue
                if isinstance(env, dict) and any(k in env for k in _ENV_KEYS):
                    found += 1
                    yield env
        if not found:
            logger.warning("Package %s contains no AAS environment part — nothing imported", label)

    @staticmethod
    def _iter_parts(raw: bytes, label: str) -> Iterator[tuple[str, bytes]]:
        """Package entries, falling back to a local-header walk for a truncated package."""
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for name in zf.namelist():
                    yield name, zf.read(name)
            return
        except zipfile.BadZipFile:
            if not raw.startswith(b"PK\x03\x04"):
                raise
        logger.warning(
            "Package %s has no readable ZIP central directory (truncated?); "
            "reading the entries that are intact", label,
        )
        yield from _iter_local_entries(raw)

    def upload_aasx_file(self, aasx_path: str, db_batch_size: int = 1000) -> UploadStats:
        """Extract the AAS environments from an AASX package and upload them to Neo4j."""
        stats = UploadStats()
        for env in self.iter_environments(aasx_path):
            file_stats = self.xml_importer.upload_xml(env, db_batch_size=db_batch_size)
            stats.total_nodes_created += file_stats.total_nodes_created
            stats.total_relationships_created += file_stats.total_relationships_created
            stats.total_node_creation_time += file_stats.total_node_creation_time
            stats.total_relationship_creation_time += file_stats.total_relationship_creation_time
        stats.finish()
        return stats

    def upload_all_aasx_from_dir(self, directory: str, db_batch_size: int = 1000) -> UploadStats:
        """Upload all .aasx files (gzipped ones included) from a directory into Neo4j."""
        stats = UploadStats()
        aasx_files = list_aas_files(directory, suffixes=(".aasx",))
        logger.info(f"Found {len(aasx_files)} AASX files in '{directory}'")

        for path in aasx_files:
            logger.info(f"Uploading {path.name}")
            start = time.time()
            file_stats = self.upload_aasx_file(str(path), db_batch_size=db_batch_size)
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
