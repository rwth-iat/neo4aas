"""ECLASS classification-dictionary ingestion into AAS ConceptDescriptions.

Parses ECLASS BASIC/ADVANCED OntoML XML exports (ECLASS XML 5.0) into typed
models, which downstream modules turn into spec-conformant AAS
``ConceptDescription`` objects (``DataSpecificationIec61360``) and push through
the existing ``AASNeo4JClient``.
"""

from aas_mapping.eclass.models import (
    EclassClass,
    EclassProperty,
    EclassUnit,
    LangText,
    ParseResult,
)
from aas_mapping.eclass.ontoml_parser import EclassOntomlParser
from aas_mapping.eclass.spec_cd_writer import SpecCdWriter
from aas_mapping.eclass.derive_edges import derive_has_property_edges
from aas_mapping.eclass.ingest import (
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
