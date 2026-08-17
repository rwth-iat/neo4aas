"""ECLASS classification-dictionary ingestion into AAS ConceptDescriptions.

Parses ECLASS BASIC/ADVANCED OntoML XML exports (ECLASS XML 5.0) into typed
models, which downstream modules turn into spec-conformant AAS
``ConceptDescription`` objects (``DataSpecificationIec61360``) and push through
the existing ``AASNeo4JClient``.
"""

from neo4aas.eclass.models import (
    EclassClass,
    EclassProperty,
    EclassUnit,
    LangText,
    ParseResult,
)
from neo4aas.eclass.ontoml_parser import EclassOntomlParser
from neo4aas.eclass.spec_cd_writer import SpecCdWriter
from neo4aas.eclass.derive_edges import derive_has_property_edges
from neo4aas.eclass.ingest import (
    build_concept_descriptions,
    ingest_eclass,
    push_concept_descriptions,
)

__all__ = [
    "EclassClass",
    "EclassProperty",
    "EclassUnit",
    "LangText",
    "ParseResult",
    "EclassOntomlParser",
    "SpecCdWriter",
    "build_concept_descriptions",
    "push_concept_descriptions",
    "ingest_eclass",
    "derive_has_property_edges",
]
