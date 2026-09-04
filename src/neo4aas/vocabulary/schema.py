"""Constraints and indexes for the vocabulary graph.

Every ``MERGE`` in :mod:`neo4aas.vocabulary.load` keys on one of the properties
below. **Without a uniqueness constraint a ``MERGE`` is a full label scan**, and
a load that merges a few hundred thousand shapes against a growing store never
finishes — so :func:`create_schema` runs before the first write, not after it.

The constraints double as the correctness statement: two ``:Shape`` nodes with
one ``shape_key`` would mean the hash is not a function of its input, and the
database refuses to store that rather than quietly producing a graph whose counts
change on re-import.
"""

from __future__ import annotations

#: ``(name, label, property)`` — one uniqueness constraint per node key.
KEY_CONSTRAINTS: tuple[tuple[str, str, str], ...] = (
    ("vocab_shape_key", "Shape", "shape_key"),
    ("vocab_concept_id", "Concept", "id"),
    ("vocab_template_id", "Template", "template_id"),
    ("vocab_vendor_name", "Vendor", "name"),
    ("vocab_semantic_id_value", "SemanticId", "value"),
)

#: ``(name, label, property)`` — the lookups the load and the traversals do.
#: ``Concept.id_base`` backs semanticId resolution (the version-stripped join,
#: which lifts the resolution rate roughly 10x) and ``Concept.coded_name`` backs
#: the ECLASS prefix hierarchy. ``Shape.id_short_norm`` and ``Shape.id_short_base``
#: are the idShort join: matching a spelling across vendors is an equality on the
#: first, grouping a shape's variants is an equality on the second, and both are
#: only useful if they are indexed — an unindexed grouping is a full label scan of
#: the whole shape vocabulary.
#: ``SubmodelGroup.id_short_base`` is the merged submodel parents' own lookup —
#: "what does a Nameplate submodel directly contain" starts there — and a
#: label-scoped index is what keeps it a seek rather than a scan of the whole shape
#: vocabulary the ``:Shape`` index covers.
LOOKUP_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("vocab_submodel_group_base", "SubmodelGroup", "id_short_base"),
    ("vocab_concept_id_base", "Concept", "id_base"),
    ("vocab_concept_coded_name", "Concept", "coded_name"),
    ("vocab_concept_kind", "Concept", "kind"),
    ("vocab_semantic_id_value_base", "SemanticId", "value_base"),
    ("vocab_shape_id_short", "Shape", "id_short"),
    ("vocab_shape_id_short_norm", "Shape", "id_short_norm"),
    ("vocab_shape_id_short_base", "Shape", "id_short_base"),
    ("vocab_template_family", "Template", "family"),
)

#: A TEXT index supports ``CONTAINS`` / ``ENDS WITH``, which a range index does
#: not — and the first hop of the query-expansion traversal is a substring match
#: on ``Shape.id_short`` over the whole shape vocabulary.
TEXT_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("vocab_shape_id_short_text", "Shape", "id_short"),
    ("vocab_shape_id_short_norm_text", "Shape", "id_short_norm"),
)

#: Labels this importer owns. ``vocab clear`` removes these and nothing else, so a
#: vocabulary graph can share a database with anything.
#:
#: ``SubmodelGroup`` is a *second* label on a ``:Shape`` node, not a node of its
#: own, so it is listed **first**: ``clear`` counts each label before deleting it,
#: and deleting ``:Shape`` first would report zero groups removed. For the same
#: reason a group is counted twice by :meth:`VocabularyLoader.node_counts` — once
#: as what it is, once as what it is written as.
VOCABULARY_LABELS: tuple[str, ...] = (
    "SubmodelGroup", "Shape", "SemanticId", "Concept", "Template", "Vendor",
)

#: Relationship types this importer owns, for reporting and for ``clear``.
VOCABULARY_RELATIONSHIPS: tuple[str, ...] = (
    "DECLARES", "DECLARES_SUPPLEMENTAL", "RESOLVES_TO", "BROADER", "CONTAINS",
    "DECLARES_SHAPE", "USES",
)


#: Labels and relationship types this importer used to own — the ``:Structure``
#: layout model, and ``:PRECEDES`` sibling adjacency. ``vocab clear`` removes them
#: too, so upgrading a database written by an earlier version is one ``clear`` and
#: one re-import rather than a manual cleanup. Nothing else refers to them — they
#: are not reported and never written.
LEGACY_LABELS: tuple[str, ...] = ("Structure",)
LEGACY_RELATIONSHIPS: tuple[str, ...] = ("HAS_SLOT", "SHIPS", "PRECEDES")

#: Constraints and indexes this importer used to create, under property names it no
#: longer writes: ``Concept.irdi`` / ``Concept.irdi_base`` (renamed to ``id`` /
#: ``id_base`` — most concepts are identified by a URL, not an IRDI) and
#: ``SemanticId.base`` (``value_base``). A uniqueness constraint on a property nothing
#: writes any more is not harmless: every node satisfies it with ``null``, so it lingers
#: as a false statement about the model and shows up in ``SHOW CONSTRAINTS``. ``vocab
#: clear`` drops them, so upgrading is one ``clear`` and one re-import.
LEGACY_CONSTRAINTS: tuple[str, ...] = ("vocab_concept_irdi",)
LEGACY_INDEXES: tuple[str, ...] = (
    "vocab_concept_irdi_base", "vocab_semantic_id_base",
)


def constraint_statements() -> list[str]:
    return [
        f"CREATE CONSTRAINT {name} IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
        for name, label, prop in KEY_CONSTRAINTS
    ]


def index_statements() -> list[str]:
    return [
        f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
        for name, label, prop in LOOKUP_INDEXES
    ] + [
        f"CREATE TEXT INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
        for name, label, prop in TEXT_INDEXES
    ]


def drop_legacy_statements() -> list[str]:
    """Drop what an earlier version of this schema created. Idempotent."""
    return [f"DROP CONSTRAINT {name} IF EXISTS" for name in LEGACY_CONSTRAINTS] + \
           [f"DROP INDEX {name} IF EXISTS" for name in LEGACY_INDEXES]


def schema_statements() -> list[str]:
    """Constraints first: an index on a property that also has a constraint is redundant."""
    return constraint_statements() + index_statements()
