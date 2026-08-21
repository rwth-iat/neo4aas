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
    ("vocab_concept_irdi", "Concept", "irdi"),
    ("vocab_template_id", "Template", "template_id"),
    ("vocab_vendor_name", "Vendor", "name"),
    ("vocab_semantic_id_value", "SemanticId", "value"),
)

#: ``(name, label, property)`` — the lookups the load and the traversals do.
#: ``Concept.irdi_base`` backs semanticId resolution (the version-stripped join,
#: which lifts the resolution rate roughly 10x) and ``Concept.coded_name`` backs
#: the ECLASS prefix hierarchy.
LOOKUP_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("vocab_concept_irdi_base", "Concept", "irdi_base"),
    ("vocab_concept_coded_name", "Concept", "coded_name"),
    ("vocab_concept_kind", "Concept", "kind"),
    ("vocab_semantic_id_base", "SemanticId", "base"),
    ("vocab_shape_id_short", "Shape", "id_short"),
    ("vocab_template_family", "Template", "family"),
)

#: A TEXT index supports ``CONTAINS`` / ``ENDS WITH``, which a range index does
#: not — and the first hop of the query-expansion traversal is a substring match
#: on ``Shape.id_short`` over the whole shape vocabulary.
TEXT_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("vocab_shape_id_short_text", "Shape", "id_short"),
)

#: Labels this importer owns. ``vocab clear`` removes these and nothing else, so a
#: vocabulary graph can share a database with anything.
VOCABULARY_LABELS: tuple[str, ...] = (
    "Shape", "SemanticId", "Concept", "Template", "Vendor",
)

#: Relationship types this importer owns, for reporting and for ``clear``.
VOCABULARY_RELATIONSHIPS: tuple[str, ...] = (
    "DECLARES", "RESOLVES_TO", "BROADER", "CONTAINS", "PRECEDES",
    "DECLARES_SHAPE", "USES",
)


#: Labels and relationship types this importer used to own. ``vocab clear`` removes
#: them too, so upgrading a database written by the ``:Structure`` version is one
#: ``clear`` and one re-import rather than a manual cleanup. Nothing else refers to
#: them — they are not reported and never written.
LEGACY_LABELS: tuple[str, ...] = ("Structure",)
LEGACY_RELATIONSHIPS: tuple[str, ...] = ("HAS_SLOT", "SHIPS")


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


def schema_statements() -> list[str]:
    """Constraints first: an index on a property that also has a constraint is redundant."""
    return constraint_statements() + index_statements()
