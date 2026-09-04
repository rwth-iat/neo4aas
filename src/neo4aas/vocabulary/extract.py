"""Documents in, vocabulary out. Pure — this module never touches a driver.

The whole point of the vocabulary graph is that what it holds is *distinct
things*, not occurrences, so extraction is a fold: a stream of AAS documents
collapses into a small set of shapes and the containment relations between them,
plus integer counters. Two folds merge (:meth:`VocabularyRecords.merge`), which
is what lets extraction run in worker processes and the result be summed in the
parent.

Layout is edges, not nodes
--------------------------

A submodel's layout is stored as ``(:Shape)-[:CONTAINS]->(:Shape)`` merged over
every submodel in the corpus, not as a per-layout node. That is a deliberate
trade, and both halves of it are measured (``docs/vocabulary-graph.md``):

* the merged form is **78x smaller** — 9 935 edges against 773 566 slot edges on
  the 5 000-file load;
* it is a *union*, so it cannot say which layouts exist. Of 1 448 parent shapes,
  279 have more than one distinct child set, so a path read off the merged graph
  may correspond to no real submodel.

What keeps the merge honest is **support**: every edge records how many
occurrences and how many submodels it was seen in, per vendor. A pair seen in
12 000 submodels is schema; one seen in three is noise, and the graph says which
is which even though it can no longer enumerate layouts.

Keys
----

``shape_key`` is a SHA-256 over the canonical JSON of
``[idShort, semanticId, modelType, valueType]`` — **verbatim** values, no
normalisation, no case folding, no trimming. It is a pure function of its input,
so the same corpus produces the same keys on any machine and any run, which is
what makes the whole import idempotent.

``semanticId`` is ``semanticId.keys[0].value`` and nothing else. ``Key.type``
disagrees across vendors for identical values (one vendor writes
``ConceptDescription``, another ``GlobalReference`` for the same IRDI), so it is
recorded on the ``:SemanticId`` node and ignored for matching.

``submodel_group_key`` is the **second** key, and the only other one. It keys the
merged parent a submodel's top-level elements hang off
(:class:`SubmodelGroup`) and it is deliberately *not* a ``shape_key``: it hashes
a different payload (a literal marker, the ``id_short_base`` fold, the
semanticId) so the two can never be confused or collide. ``shape_key`` is a
cross-repository seam — discaas2 computes the same SHA-256 over the same
verbatim values — and changing it would orphan every node in every database that
already holds this vocabulary, so the merged parent is an *additional* node
beside the per-product Submodel shape rather than a replacement for it.

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
from typing import Any, Iterable, Iterator

from neo4aas.core.io import aas_suffix, is_aas_file, read_bytes
from neo4aas.core.utils import id_base
from neo4aas.vocabulary.idshort import id_short_base, normalize_id_short

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


@dataclass(frozen=True, slots=True)
class Shape:
    """One distinct element shape. A few thousand describe tens of millions of elements."""

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

    #: The two derived forms are *properties*, not fields, for two reasons: a field
    #: would be computed in :meth:`of` — once per element, tens of millions of times,
    #: where these are read once per *distinct* shape at write time — and it would
    #: travel in the pickle a ``--workers`` run sends back from every batch. Neither
    #: is part of :func:`shape_key`; see the module docstring.
    @property
    def id_short_norm(self) -> str | None:
        """:func:`~neo4aas.vocabulary.idshort.normalize_id_short` of :attr:`id_short`."""
        return normalize_id_short(self.id_short)

    @property
    def id_short_base(self) -> str | None:
        """:func:`~neo4aas.vocabulary.idshort.id_short_base` of :attr:`id_short`."""
        return id_short_base(self.id_short)


@dataclass(frozen=True, slots=True)
class ShapeUse:
    """One occurrence's shape plus the detail the shape key does not carry.

    ``category`` is not part of the key (the key is fixed by design) and the two
    ``Reference`` type fields are recorded on the ``:SemanticId`` node but never
    used for matching — vendors disagree about them for identical values. They are
    collected during the single structural walk rather than by a second pass.
    """

    shape: Shape
    category: str | None
    key_type: str | None
    ref_type: str | None
    #: ``(value, Key.type, Reference.type)`` per ``supplementalSemanticIds`` entry —
    #: "this element is *also* that concept", which is where a vendor bridges two
    #: dictionaries. Sorted, because they are a set: an element's supplementals carry
    #: no order, and sorting is what makes the fold independent of document order.
    #: Deliberately **not** part of :attr:`shape`'s key — see
    #: :func:`neo4aas.vocabulary.extract.shape_key`.
    supplemental: tuple[tuple[str, str | None, str | None], ...] = ()


def submodel_group_key(base: str | None, semantic_id: str | None) -> str:
    """Stable key of the *merged* parent of a submodel's top-level elements.

    A different payload from :func:`shape_key` — a literal marker, two components
    instead of four — so a group key can never equal a shape key, and a reader
    holding one can always tell which it has.
    """
    return _digest(["submodel-group", base, semantic_id])


@dataclass(frozen=True, slots=True)
class SubmodelGroup:
    """The parent of a submodel's top-level elements, merged across products.

    The submodel's *own* shape cannot be that parent. Several vendors put a
    per-product token in the submodel ``idShort`` — SICK ships
    ``Nameplate_0226303791c8a2efe04ed3e83865b6b7`` — and ``shape_key`` hashes the
    ``idShort`` verbatim, so rooting containment there gives every product its own
    subtree: 1 670 321 edges and 140 515 single-product parents on the t100k tier,
    against ~2 195 for this key.

    The key is ``(id_short_base, semanticId)`` and both halves are load-bearing:

    * ``semanticId`` alone strands the 1 156 t100k submodels that declare none,
      and their top-level elements would stay parentless;
    * ``id_short_base`` alone merges submodels that are genuinely different — two
      vendors can both ship an ``idShort`` folding to ``nameplate`` while
      declaring different template ``semanticId``s.

    Together they cost 28 edges over ``semanticId`` alone. Where *both* are
    missing there is nothing left to merge on, so :meth:`of` returns ``None`` and
    that submodel keeps the pre-existing behaviour: roots counted, no edge. It is
    the only case that does.
    """

    key: str
    #: :func:`~neo4aas.vocabulary.idshort.id_short_base` of the submodel's idShort.
    #: A *field*, not a property as on :class:`Shape`: there is no verbatim idShort
    #: on this node to derive it from, and it is what the node is looked up by.
    id_short_base: str | None
    semantic_id: str | None

    @classmethod
    def of(cls, submodel: dict) -> SubmodelGroup | None:
        """The merged parent of one Submodel, or ``None`` when it cannot merge."""
        base = id_short_base(_text(submodel.get("idShort")))
        semantic = semantic_id_of(submodel)
        if base is None and semantic is None:
            return None
        return cls(submodel_group_key(base, semantic), base, semantic)


@dataclass
class SubmodelWalk:
    """What one submodel contributes: shapes, roots and containment.

    Containment is counted *per submodel* so that ``:CONTAINS`` can carry two
    different supports — total occurrences, and the number of submodels the pair
    was seen in. The second is what separates a real schema relation from a
    one-off, and it is the only defence the merged form has against the phantom
    combinations it cannot rule out.
    """

    uses: list[ShapeUse] = field(default_factory=list)
    #: Shapes appearing directly under the Submodel. Kept as a count *as well as* an
    #: edge from :attr:`group`: it is what ``Shape.top_level`` and the ``:USES``
    #: edge's ``top_level`` are computed from, and existing consumers read them.
    roots: Counter = field(default_factory=Counter)
    #: The merged parent the top level hangs off, or ``None`` when this submodel has
    #: neither an ``idShort`` nor a ``semanticId`` to merge on.
    group: SubmodelGroup | None = None
    #: ``(parent_shape_key, child_shape_key) -> occurrences in this submodel``.
    contains: Counter = field(default_factory=Counter)
    elements: int = 0


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
class SemanticIdRecord:
    """A declared semantic identifier. Stays in the graph whether it resolves or not.

    ~31 % resolve nowhere, and that absence is a finding — a vendor pointing at an
    identifier no dictionary defines — not an ingest error.
    """

    value: str
    #: The version-stripped join key (:func:`neo4aas.core.utils.id_base`). Named for
    #: the field it derives from — a ``:SemanticId`` has one other property that could
    #: have a base, and the reader should not have to guess which.
    value_base: str
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
    """The fold. Merges associatively, so workers can extract in parallel.

    Every counter is keyed by **vendor first**. That is not bookkeeping — it is
    what makes the write idempotent. ``:CONTAINS`` is a single edge shared by
    every vendor that ships the pair, so a run cannot simply add to a number it
    has no way of telling apart from history; it replaces its own vendor's slot
    (see :mod:`neo4aas.vocabulary.load`).
    """

    shapes: dict[str, ShapeRecord] = field(default_factory=dict)
    #: ``submodel_group_key -> SubmodelGroup``. The merged containment parents, held
    #: apart from :attr:`shapes` because they are written by their own statement:
    #: a group has no verbatim ``idShort`` to derive ``id_short_norm`` from, and no
    #: ``valueType`` or ``category``. They carry no counters of their own — the
    #: support for a merged parent lives on its ``:CONTAINS`` edges, per vendor,
    #: which is the same answer this design gives everywhere else.
    submodel_groups: dict[str, SubmodelGroup] = field(default_factory=dict)
    semantic_ids: dict[str, SemanticIdRecord] = field(default_factory=dict)
    #: ``(vendor, shape_key) -> occurrences`` — the ``:USES`` edge payload.
    vendor_shapes: Counter = field(default_factory=Counter)
    #: ``(vendor, shape_key) -> occurrences at the top level of a submodel``.
    vendor_roots: Counter = field(default_factory=Counter)
    #: ``(vendor, parent_key, child_key) -> element occurrences``.
    contains: Counter = field(default_factory=Counter)
    #: ``(vendor, parent_key, child_key) -> submodels the pair was seen in``.
    contains_submodels: Counter = field(default_factory=Counter)
    #: ``(shape_key, value)`` — the ``:DECLARES_SUPPLEMENTAL`` edge. A **set**, not a
    #: counter: like ``:DECLARES`` it carries no support and no vendor, so there is
    #: nothing a re-import could drift. One shape accumulates the union of the
    #: supplementals its occurrences declare, since they are not part of its key.
    supplemental_declarations: set[tuple[str, str]] = field(default_factory=set)
    #: ``(parent_key, child_key)`` pairs an IDTA template declares. No vendor: a
    #: template is not shipped by anyone, it is what shipping is measured against.
    template_contains: set = field(default_factory=set)
    #: ``id -> concept dict``, harvested from ConceptDescriptions embedded in the
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
        self.submodel_groups.update(other.submodel_groups)
        for value, sem in other.semantic_ids.items():
            if value in self.semantic_ids:
                self.semantic_ids[value].absorb(sem)
            else:
                self.semantic_ids[value] = sem
        for identifier, concept in other.concepts.items():
            self.concepts.setdefault(identifier, concept)
        self.vendor_shapes.update(other.vendor_shapes)
        self.vendor_roots.update(other.vendor_roots)
        self.contains.update(other.contains)
        self.contains_submodels.update(other.contains_submodels)
        self.supplemental_declarations |= other.supplemental_declarations
        self.template_contains |= other.template_contains
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

    def add_submodel_group(self, group: SubmodelGroup | None) -> None:
        """Record a merged containment parent. Idempotent — the key is its identity."""
        if group is not None:
            self.submodel_groups.setdefault(group.key, group)

    def add_semantic_id(self, value: str, key_type: str | None, ref_type: str | None) -> None:
        rec = self.semantic_ids.get(value)
        if rec is None:
            rec = self.semantic_ids[value] = SemanticIdRecord(value, id_base(value))
        if key_type:
            rec.key_types.add(key_type)
        if ref_type:
            rec.ref_types.add(ref_type)

    def add_supplementals(self, use: ShapeUse) -> None:
        """Record one occurrence's supplemental ids against its shape."""
        for value, key_type, ref_type in use.supplemental:
            self.add_semantic_id(value, key_type, ref_type)
            self.supplemental_declarations.add((use.shape.key, value))

    def add_walk(self, walk: SubmodelWalk, vendor: str) -> None:
        """Fold one submodel's walk in under ``vendor``."""
        for use in walk.uses:
            self.add_shape(use.shape, vendor, use.category)
            if use.shape.semantic_id:
                self.add_semantic_id(use.shape.semantic_id, use.key_type, use.ref_type)
            self.add_supplementals(use)
        self.add_submodel_group(walk.group)
        for key, count in walk.roots.items():
            self.vendor_roots[(vendor, key)] += count
        for pair, count in walk.contains.items():
            self.contains[(vendor, *pair)] += count
            self.contains_submodels[(vendor, *pair)] += 1
        self.submodels += 1
        self.elements += walk.elements


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


def supplemental_semantic_id_parts(
        element: Any) -> list[tuple[str, str | None, str | None]]:
    """``(value, Key.type, Reference.type)`` for every ``supplementalSemanticIds`` entry.

    Sorted, and entries without a usable first key are dropped — the same tolerance
    :func:`semantic_id_parts` applies to the primary one.
    """
    parts: list[tuple[str, str | None, str | None]] = []
    for reference in _as_list((element or {}).get("supplementalSemanticIds")):
        key = _first_key(reference)
        value = _text(key.get("value")) if key else None
        if not value:
            continue
        ref_type = _text(reference.get("type")) if isinstance(reference, dict) else None
        parts.append((value, _text(key.get("type")), ref_type))
    return sorted(set(parts))


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


def walk_submodel(submodel: dict) -> SubmodelWalk:
    """Walk a Submodel into shapes, roots and containment pairs.

    One pass produces everything the fold needs — this is the extractor's hot loop
    and the corpus is 150 000 documents.

    The Submodel's own shape is recorded (it declares the submodel's semanticId)
    but is **not** the parent of the top-level elements. Several vendors put a
    per-product token in the submodel ``idShort`` — SICK ships
    ``Nameplate_0226303791c8a2efe04ed3e83865b6b7`` — so rooting containment there
    would give every product its own subtree and defeat the merge entirely.

    The parent is :class:`SubmodelGroup` instead: the same submodel keyed by
    ``(id_short_base, semanticId)``, which is what survives the merge. The top
    level stays a count as well (:attr:`SubmodelWalk.roots`), because that is what
    ``Shape.top_level`` is computed from and what current consumers read.
    """
    walk = SubmodelWalk()

    def record(element: dict, model_type: str | None = None) -> Shape:
        shape = Shape.of(element, model_type)
        _, key_type, ref_type = semantic_id_parts(element)
        walk.uses.append(ShapeUse(shape, _text(element.get("category")), key_type, ref_type,
                                  tuple(supplemental_semantic_id_parts(element))))
        return shape

    def visit(element: dict) -> Shape:
        shape = record(element)
        walk.elements += 1
        for child in _child_elements(element):
            child_shape = visit(child)
            walk.contains[(shape.key, child_shape.key)] += 1
        return shape

    record(submodel, "Submodel")
    walk.group = SubmodelGroup.of(submodel)
    for element in _child_elements(submodel):
        child_shape = visit(element)
        walk.roots[child_shape.key] += 1
        if walk.group is not None:
            walk.contains[(walk.group.key, child_shape.key)] += 1
    return walk


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
        if isinstance(submodel, dict):
            into.add_walk(walk_submodel(submodel), vendor)

    for description in _as_list(env.get(ENV_CONCEPT_DESCRIPTIONS)):
        concept = concept_from_description(description)
        if concept:
            into.concepts.setdefault(concept["id"], concept)

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

    jobs = [(str(root), str(path), vendor_from_dir, vendor)
            for root, path in files]
    total = VocabularyRecords()
    chunk = max(1, min(256, len(jobs) // (workers * 8) or 1))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for partial in pool.map(_extract_one, jobs, chunksize=chunk):
            total.merge(partial)
            if progress:
                progress(total)
    return total
