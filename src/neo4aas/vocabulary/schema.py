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
    ("vocab_structure_key", "Structure", "structure_key"),
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

#: Labels this importer owns. ``vocab clear`` removes these and nothing else, so a
#: vocabulary graph can share a database with anything.
VOCABULARY_LABELS: tuple[str, ...] = (
    "Shape", "Structure", "SemanticId", "Concept", "Template", "Vendor",
)

#: Relationship types this importer owns, for reporting.
VOCABULARY_RELATIONSHIPS: tuple[str, ...] = (
    "DECLARES", "RESOLVES_TO", "BROADER", "HAS_SLOT", "DECLARES_SHAPE", "USES", "SHIPS",
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
    ]


def schema_statements() -> list[str]:
    """Constraints first: an index on a property that also has a constraint is redundant."""
    return constraint_statements() + index_statements()
