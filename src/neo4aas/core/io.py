"""Reading AAS source files, transparently through gzip.

Real AAS corpora are stored compressed — the reference corpus keeps every instance as
``x.json.gz`` / ``x.xml.gz`` / ``x.aasx.gz`` (147 GB raw, 56 GB on disk) — so an ingest
path that globs ``*.json`` finds nothing there and reports "0 files" rather than an
error. Every neo4aas ingest entry point reads through the helpers here instead.

Compression is detected by the **gzip magic bytes**, not by the file name: a corpus may
strip or keep the ``.gz`` suffix independently of the actual encoding. The *AAS* format
is taken from the name with any ``.gz`` removed (:func:`aas_suffix`).
"""

import gzip
from pathlib import Path
from typing import Iterable, List

#: Leading bytes of a gzip member (RFC 1952).
GZIP_MAGIC = b"\x1f\x8b"

#: AAS source formats neo4aas can ingest.
AAS_SUFFIXES = (".json", ".xml", ".aasx")

PathLike = str | Path


def read_bytes(path: PathLike) -> bytes:
    """Return the file's content, decompressed when it is gzipped."""
    data = Path(path).read_bytes()
    if data[:2] == GZIP_MAGIC:
        return gzip.decompress(data)
    return data


def aas_suffix(path: PathLike) -> str:
    """Lower-cased AAS format suffix of ``path``, ignoring a trailing ``.gz``.

    ``"a.json"`` and ``"a.json.gz"`` both give ``".json"``; a name with no AAS suffix
    gives whatever its own suffix is (``""`` when it has none).
    """
    name = Path(path).name
    if name.lower().endswith(".gz"):
        name = name[: -len(".gz")]
    return Path(name).suffix.lower()


def is_aas_file(path: PathLike, suffixes: Iterable[str] = AAS_SUFFIXES) -> bool:
    """True when ``path`` names an AAS source file of one of ``suffixes`` (gz-aware)."""
    return aas_suffix(path) in tuple(suffixes)


def list_aas_files(directory: PathLike, suffixes: Iterable[str] = AAS_SUFFIXES,
                   recursive: bool = False) -> List[Path]:
    """Sorted AAS source files in ``directory`` (gzipped ones included)."""
    root = Path(directory)
    entries = root.rglob("*") if recursive else root.iterdir()
    return sorted(p for p in entries if p.is_file() and is_aas_file(p, suffixes))
