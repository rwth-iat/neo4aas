"""Documents in, vocabulary out. Pure — this module never touches a driver.

The whole point of the vocabulary graph is that what it holds is *distinct
things*, not occurrences, so extraction is a fold: a stream of AAS documents
collapses into a small set of shapes, structures and semanticIds plus integer
counters. Two folds merge (:meth:`VocabularyRecords.merge`), which is what lets
extraction run in worker processes and the result be summed in the parent.

Keys
----

``shape_key`` is a SHA-256 over the canonical JSON of
``[idShort, semanticId, modelType, valueType]`` — **verbatim** values, no
normalisation, no case folding, no trimming. ``structure_key`` is a SHA-256 over
the canonical JSON of the structure's ordered ``shape_key`` sequence. Both are
pure functions of their input, so the same corpus produces the same keys on any
machine and any run, which is what makes the whole import idempotent.

``semanticId`` is ``semanticId.keys[0].value`` and nothing else. ``Key.type``
disagrees across vendors for identical values (one vendor writes
``ConceptDescription``, another ``GlobalReference`` for the same IRDI), so it is
recorded on the ``:SemanticId`` node and ignored for matching.

What is *not* extracted
-----------------------

Values. Descriptions. Display names. Ids. Anything that varies per product does
not belong in this graph — it is the instance importer's job and PostgreSQL's.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from neo4aas.core.io import aas_suffix, is_aas_file, read_bytes
from neo4aas.core.utils import irdi_base

#: Attributes whose value is a list of contained SubmodelElements. ``value`` is in
#: here for SubmodelElementCollection/List; a Property's scalar ``value`` and a
#: MultiLanguageProperty's LangString list are excluded by the ``modelType`` guard
#: in :func:`_child_elements`, not by name — the same key means different things
#: on different types and only the child's own shape can tell them apart.
CHILD_KEYS: tuple[str, ...] = ("submodelElements", "value", "statements", "annotations")

#: Operation variables wrap their element one level deeper: ``{"value": {...}}``.
OPERATION_VAR_KEYS: tuple[str, ...] = ("inputVariables", "outputVariables", "inoutputVariables")

#: Top-level Environment keys that hold Identifiables.
ENV_SUBMODELS = "submodels"
ENV_SHELLS = "assetAdministrationShells"
ENV_CONCEPT_DESCRIPTIONS = "conceptDescriptions"


def loads_aas_json(raw: bytes) -> Any:
    """Parse AAS JSON, tolerating the UTF-8 BOM real exports are written with.

    ``json.loads`` on BOM-prefixed bytes raises, and both the IDTA templates and
    parts of the corpus ship that way.
    """
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    return json.loads(raw)


def _digest(payload: Any) -> str:
    """SHA-256 over canonical JSON — stable across machines, runs and Python builds."""
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def shape_key(id_short: str | None, semantic_id: str | None,
              model_type: str | None, value_type: str | None) -> str:
    """Stable key of an element shape. Values go in verbatim (see module docstring)."""
    return _digest([id_short, semantic_id, model_type, value_type])


def structure_key(shape_keys: Sequence[str]) -> str:
    """Stable key of a submodel structure: its ordered sequence of shape keys."""
    return _digest(list(shape_keys))


@dataclass(frozen=True, slots=True)
class Shape:
    """One distinct element shape. 4 686 of these describe 860 260 elements."""

    key: str
    id_short: str | None
    semantic_id: str | None
    model_type: str | None
    value_type: str | None

    @classmethod
    def of(cls, element: dict, model_type: str | None = None) -> Shape:
        """The shape of one AAS element dict (or Submodel)."""
        id_short = _text(element.get("idShort"))
        semantic = semantic_id_of(element)
        mt = model_type or _text(element.get("modelType"))
        value_type = _text(element.get("valueType"))
        return cls(shape_key(id_short, semantic, mt, value_type),
                   id_short, semantic, mt, value_type)


@dataclass(frozen=True, slots=True)
class ShapeUse:
    """One slot's shape plus the per-occurrence detail the shape does not carry.

    ``category`` is not part of the shape key (the key is fixed by design) and the
    two ``Reference`` type fields are recorded on the ``:SemanticId`` node but never
    used for matching — vendors disagree about them for identical values. They are
    collected during the single structural walk rather than by a second pass.
    """

    shape: Shape
    category: str | None
    key_type: str | None
    ref_type: str | None


@dataclass(frozen=True, slots=True)
class Slot:
    """A position inside a structure: which shape sits where, under whom."""

    ord: int
    depth: int
    parent_ord: int | None
    shape_key: str


@dataclass(frozen=True, slots=True)
class Structure:
    """One distinct submodel structure: the ordered shape sequence of its elements.

    The Submodel's *own* shape is deliberately not a slot. It was, in an earlier
    version, on the argument that it makes a structure self-describing — and it
    measurably broke the thing structures exist for. Several vendors put a
    per-product token in the submodel's ``idShort`` (SICK ships
    ``Nameplate_0226303791c8a2efe04ed3e83865b6b7``), so a head slot gives every
    product its own structure: on a 400-file sample, 584 structures with the head
    against 452 without, and the gap widens with corpus size. Structures are the
    corpus's *layouts*, and a layout must not carry a serial number.

    The submodel's shape is still recorded as a :class:`Shape` — the vendor uses it
    and it declares the submodel's semanticId — it simply is not part of the layout.
    """

    key: str
    slots: tuple[Slot, ...]

    @property
    def element_count(self) -> int:
        """SubmodelElements at every depth — one ``:HAS_SLOT`` edge each."""
        return len(self.slots)

    @property
    def slot_count(self) -> int:
        """Top-level slots: how wide the submodel is before any nesting."""
        return sum(1 for slot in self.slots if slot.depth == 0)


@dataclass
class ShapeRecord:
    """A shape plus what the corpus says about it."""

    shape: Shape
    occurrences: int = 0
    #: Lexicographically smallest ``category`` seen. Not part of the key (the key is
    #: fixed by design), so two elements can disagree; taking the minimum makes the
    #: stored value independent of ingest order, which keeps re-imports identical.
    category: str | None = None

    def absorb(self, other: ShapeRecord) -> None:
        self.occurrences += other.occurrences
        self.category = _min_opt(self.category, other.category)


@dataclass
class StructureRecord:
    structure: Structure
    occurrences: int = 0

    def absorb(self, other: StructureRecord) -> None:
        self.occurrences += other.occurrences


@dataclass
class SemanticIdRecord:
    """A declared semantic identifier. Stays in the graph whether it resolves or not.

    ~31 % resolve nowhere, and that absence is a finding — a vendor pointing at an
    identifier no dictionary defines — not an ingest error.
    """

    value: str
    base: str
    #: Every ``Key.type`` seen for this value. Vendors disagree; that disagreement is
    #: data quality, so it is recorded and never used for matching.
    key_types: set[str] = field(default_factory=set)
    #: Every ``Reference.type`` seen (``ModelReference`` / ``ExternalReference``).
    ref_types: set[str] = field(default_factory=set)

    def absorb(self, other: SemanticIdRecord) -> None:
        self.key_types |= other.key_types
        self.ref_types |= other.ref_types


@dataclass
class VocabularyRecords:
    """The fold. Merges associatively, so workers can extract in parallel."""

    shapes: dict[str, ShapeRecord] = field(default_factory=dict)
    structures: dict[str, StructureRecord] = field(default_factory=dict)
    semantic_ids: dict[str, SemanticIdRecord] = field(default_factory=dict)
    #: ``(vendor, shape_key) -> occurrences`` — the ``:USES`` edge payload.
    vendor_shapes: Counter = field(default_factory=Counter)
    #: ``(vendor, structure_key) -> submodels`` — the ``:SHIPS`` edge payload.
    vendor_structures: Counter = field(default_factory=Counter)
    #: ``irdi -> concept dict``, harvested from ConceptDescriptions embedded in the
    #: documents. For a large share of distinct semanticIds this is the *only*
    #: definition source in the corpus (see :mod:`neo4aas.vocabulary.concepts`).
    concepts: dict[str, dict] = field(default_factory=dict)
    vendors: set[str] = field(default_factory=set)
    documents: int = 0
    shells: int = 0
    submodels: int = 0
    elements: int = 0
    #: ``(path, error)`` for every document that would not parse. Ingest reports and
    #: continues; a corpus with 5 000 truncated packages must not stop the load.
    failures: list[tuple[str, str]] = field(default_factory=list)

    def merge(self, other: VocabularyRecords) -> VocabularyRecords:
        for key, rec in other.shapes.items():
            if key in self.shapes:
                self.shapes[key].absorb(rec)
            else:
                self.shapes[key] = rec
        for key, srec in other.structures.items():
            if key in self.structures:
                self.structures[key].absorb(srec)
            else:
                self.structures[key] = srec
        for value, sem in other.semantic_ids.items():
            if value in self.semantic_ids:
                self.semantic_ids[value].absorb(sem)
            else:
                self.semantic_ids[value] = sem
        for irdi, concept in other.concepts.items():
            self.concepts.setdefault(irdi, concept)
        self.vendor_shapes.update(other.vendor_shapes)
        self.vendor_structures.update(other.vendor_structures)
        self.vendors |= other.vendors
        self.documents += other.documents
        self.shells += other.shells
        self.submodels += other.submodels
        self.elements += other.elements
        self.failures.extend(other.failures)
        return self

    # -- accumulation -----------------------------------------------------------

    def add_shape(self, shape: Shape, vendor: str | None, category: str | None = None,
                  occurrences: int = 1) -> None:
        """Record a shape. ``vendor=None`` means "declared, not used by anyone" —
        a template shape no vendor ships, which is a finding worth keeping."""
        rec = self.shapes.get(shape.key)
        if rec is None:
            rec = self.shapes[shape.key] = ShapeRecord(shape)
        rec.occurrences += occurrences
        rec.category = _min_opt(rec.category, category)
        if vendor is not None:
            self.vendor_shapes[(vendor, shape.key)] += occurrences

    def add_structure(self, structure: Structure, vendor: str) -> None:
        rec = self.structures.get(structure.key)
        if rec is None:
            rec = self.structures[structure.key] = StructureRecord(structure)
        rec.occurrences += 1
        self.vendor_structures[(vendor, structure.key)] += 1

    def add_semantic_id(self, value: str, key_type: str | None, ref_type: str | None) -> None:
        rec = self.semantic_ids.get(value)
        if rec is None:
            rec = self.semantic_ids[value] = SemanticIdRecord(value, irdi_base(value))
        if key_type:
            rec.key_types.add(key_type)
        if ref_type:
            rec.ref_types.add(ref_type)


# -- reading -------------------------------------------------------------------


def _text(value: Any) -> str | None:
    """A string property, or None. Anything non-string is not a shape component."""
    return value if isinstance(value, str) and value != "" else None


def _min_opt(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _first_key(reference: Any) -> dict | None:
    if not isinstance(reference, dict):
        return None
    keys = reference.get("keys")
    if isinstance(keys, dict):          # xml_to_aas_json emits a lone key as a dict
        keys = [keys]
    if not isinstance(keys, list) or not keys:
        return None
    return keys[0] if isinstance(keys[0], dict) else None


def semantic_id_of(element: Any) -> str | None:
    """``semanticId.keys[0].value``, or None. The only join key this graph trusts."""
    key = _first_key((element or {}).get("semanticId"))
    return _text(key.get("value")) if key else None


def semantic_id_parts(element: Any) -> tuple[str | None, str | None, str | None]:
    """``(value, Key.type, Reference.type)`` of an element's semanticId."""
    reference = (element or {}).get("semanticId")
    key = _first_key(reference)
    if not key:
        return None, None, None
    ref_type = _text(reference.get("type")) if isinstance(reference, dict) else None
    return _text(key.get("value")), _text(key.get("type")), ref_type


def _as_list(value: Any) -> list:
    """AAS JSON lists survive the XML converter as a bare dict when they hold one item."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _child_elements(element: dict) -> Iterator[dict]:
    """Contained SubmodelElements, in document order.

    A child is anything under :data:`CHILD_KEYS` that is a dict carrying its own
    ``modelType``. That single guard is what separates a
    SubmodelElementCollection's ``value`` (children) from a Property's ``value``
    (a scalar) and a MultiLanguageProperty's ``value`` (LangStrings, no
    ``modelType``) without special-casing any type.
    """
    for key in CHILD_KEYS:
        if key not in element:
            continue
        for child in _as_list(element[key]):
            if isinstance(child, dict) and "modelType" in child:
                yield child
    for key in OPERATION_VAR_KEYS:
        for variable in _as_list(element.get(key)):
            if isinstance(variable, dict):
                inner = variable.get("value")
                if isinstance(inner, dict) and "modelType" in inner:
                    yield inner


def structure_of(submodel: dict) -> tuple[Structure, list[ShapeUse]]:
    """Walk a Submodel into its structure and the shapes it uses.

    Pre-order, ``ord`` running over the whole submodel, slot 0 the Submodel itself.
    One walk produces everything the caller needs — the structure, the shapes, and
    the per-occurrence Reference detail — because this walk is the extractor's hot
    loop and the corpus is 150 000 documents.
    """
    slots: list[Slot] = []
    uses: list[ShapeUse] = []

    def record(element: dict, model_type: str | None = None) -> Shape:
        shape = Shape.of(element, model_type)
        _, key_type, ref_type = semantic_id_parts(element)
        uses.append(ShapeUse(shape, _text(element.get("category")), key_type, ref_type))
        return shape

    def visit(element: dict, depth: int, parent_ord: int | None) -> None:
        position = len(slots)
        slots.append(Slot(position, depth, parent_ord, record(element).key))
        for child in _child_elements(element):
            visit(child, depth + 1, position)

    record(submodel, "Submodel")          # a shape, but not a slot — see Structure
    for element in _child_elements(submodel):
        visit(element, 0, None)
    return Structure(structure_key([s.shape_key for s in slots]), tuple(slots)), uses


def iter_environments(path: Path) -> Iterator[dict]:
    """Yield the AAS Environment dict(s) in a source file, gzip and AASX transparent.

    Format comes from the name with any ``.gz`` removed and the bytes are read
    through :func:`neo4aas.core.io.read_bytes`, which detects gzip by magic bytes.
    The corpus keeps every instance compressed — ``x.json`` is on disk as
    ``x.json.gz`` — and a bare ``glob("*.json")`` over it finds nothing at all.
    """
    suffix = aas_suffix(path)
    if suffix == ".json":
        yield loads_aas_json(read_bytes(path))
    elif suffix == ".xml":
        from neo4aas.core.serialization.xml.xml_to_json import xml_to_aas_json

        yield xml_to_aas_json(str(path))
    elif suffix == ".aasx":
        from neo4aas.core.serialization.aasx import AasxToNeo4jImporter

        # iter_environments() reads the package and needs no importer to do it.
        yield from AasxToNeo4jImporter().iter_environments(path)
    else:
        raise ValueError(f"not an AAS source file: {path}")


# -- extraction ----------------------------------------------------------------


def extract_environment(env: dict, vendor: str, into: VocabularyRecords) -> None:
    """Fold one Environment into ``into``. Only vocabulary comes out."""
    from neo4aas.vocabulary.concepts import concept_from_description

    if not isinstance(env, dict):
        raise ValueError("environment is not a JSON object")

    into.shells += len(_as_list(env.get(ENV_SHELLS)))

    for submodel in _as_list(env.get(ENV_SUBMODELS)):
        if not isinstance(submodel, dict):
            continue
        structure, uses = structure_of(submodel)
        into.add_structure(structure, vendor)
        into.submodels += 1
        into.elements += structure.element_count
        for use in uses:
            into.add_shape(use.shape, vendor, use.category)
            if use.shape.semantic_id:
                into.add_semantic_id(use.shape.semantic_id, use.key_type, use.ref_type)

    for description in _as_list(env.get(ENV_CONCEPT_DESCRIPTIONS)):
        concept = concept_from_description(description)
        if concept:
            into.concepts.setdefault(concept["irdi"], concept)

    into.vendors.add(vendor)


def extract_file(path: Path, vendor: str) -> VocabularyRecords:
    """Fold one source file. A document that will not parse is reported, not raised."""
    records = VocabularyRecords()
    try:
        environments = list(iter_environments(path))
    except Exception as exc:                                    # noqa: BLE001 — boundary
        records.failures.append((str(path), f"{type(exc).__name__}: {exc}"))
        return records
    for env in environments:
        try:
            extract_environment(env, vendor, records)
        except Exception as exc:                                # noqa: BLE001 — boundary
            records.failures.append((str(path), f"{type(exc).__name__}: {exc}"))
    records.documents += 1
    return records


def vendor_of(path: Path, root: Path, vendor_from_dir: bool, fallback: str) -> str:
    """Vendor name for a file: the first path segment below ``root``.

    ``instances/vendors`` as root gives ``festo`` for
    ``instances/vendors/festo/loose/x.json.gz``. A file sitting directly in the
    root takes the root's own name.
    """
    if not vendor_from_dir:
        return fallback
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return fallback
    return relative.parts[0] if len(relative.parts) > 1 else root.resolve().name


def collect_files(paths: Iterable[str | Path], limit: int | None = None,
                  stratify: bool = False) -> list[tuple[Path, Path]]:
    """``(root, file)`` pairs for every AAS source file under ``paths``, sorted.

    Sorted so that ``--limit N`` selects the same N files on every run — without
    that, importing "the same subset" twice would compare two different corpora and
    the idempotence check would mean nothing.

    ``stratify`` interleaves the files round-robin by their vendor directory before
    the limit is applied. Plain ``--limit`` takes the first N in path order, which
    on a corpus laid out as ``vendors/<name>/…`` means the first N files of the
    alphabetically first vendor — fine for a repeatable subset, useless for asking
    how the vocabulary grows, since 92 % of shapes are vendor-private and one
    vendor's tail is nearly flat. Stratifying stays deterministic.
    """
    found: list[tuple[Path, Path]] = []
    for entry in paths:
        root = Path(entry)
        if root.is_file():
            found.append((root.parent, root))
        else:
            found.extend((root, p) for p in sorted(root.rglob("*"))
                         if p.is_file() and is_aas_file(p))
    found.sort(key=lambda pair: str(pair[1]))
    if stratify:
        found = _round_robin(found)
    return found[:limit] if limit else found


def _round_robin(files: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    """Interleave by the file's first directory below its root, order preserved."""
    groups: dict[str, list[tuple[Path, Path]]] = {}
    for root, path in files:
        groups.setdefault(vendor_of(path, root, True, root.name), []).append((root, path))
    ordered = [groups[name] for name in sorted(groups)]
    out: list[tuple[Path, Path]] = []
    for index in range(max((len(g) for g in ordered), default=0)):
        for group in ordered:
            if index < len(group):
                out.append(group[index])
    return out


def extract_paths(paths: Iterable[str | Path], *, vendor_from_dir: bool = False,
                  vendor: str = "unknown", limit: int | None = None,
                  workers: int = 1, stratify: bool = False,
                  progress: Any = None) -> VocabularyRecords:
    """Fold every AAS source file under ``paths`` into one :class:`VocabularyRecords`.

    ``workers > 1`` extracts in worker processes. Extraction is pure Python and
    holds the GIL, so threads never parallelise it; a folded batch travels back as
    one pickle that is orders of magnitude smaller than the documents it came from.
    A process pool starts with ``spawn``, so the calling program must guard its
    entry point with ``if __name__ == "__main__"``.
    """
    files = collect_files(paths, limit, stratify)
    if workers > 1 and len(files) > 1:
        return _extract_parallel(files, vendor_from_dir, vendor, workers, progress)

    total = VocabularyRecords()
    for root, path in files:
        total.merge(extract_file(path, vendor_of(path, root, vendor_from_dir, vendor)))
        if progress:
            progress(total)
    return total


def _extract_one(job: tuple[str, str, bool, str]) -> VocabularyRecords:
    """Worker entry point. Module-level so it pickles."""
    root, path, vendor_from_dir, fallback = job
    file_path = Path(path)
    return extract_file(file_path, vendor_of(file_path, Path(root), vendor_from_dir, fallback))


def _extract_parallel(files: list[tuple[Path, Path]], vendor_from_dir: bool, vendor: str,
                      workers: int, progress: Any) -> VocabularyRecords:
    from concurrent.futures import ProcessPoolExecutor

    jobs = [(str(root), str(path), vendor_from_dir, vendor) for root, path in files]
    total = VocabularyRecords()
    chunk = max(1, min(256, len(jobs) // (workers * 8) or 1))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for partial in pool.map(_extract_one, jobs, chunksize=chunk):
            total.merge(partial)
            if progress:
                progress(total)
    return total
