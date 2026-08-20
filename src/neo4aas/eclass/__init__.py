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

#: Names re-exported from the basyx-dependent modules. They are resolved on first
#: attribute access (PEP 562) rather than at import time, so that
#: ``from neo4aas.eclass.ontoml_parser import EclassOntomlParser`` — parsing ECLASS
#: XML into the dataclasses in :mod:`neo4aas.eclass.models`, which needs no SDK —
#: does not drag basyx in through this ``__init__``. :mod:`neo4aas.vocabulary`
#: reuses the parser exactly that way, and importing the package eagerly would make
#: the whole vocabulary importer require the ``[basyx]`` extra for no reason.
_LAZY: dict[str, str] = {
    "SpecCdWriter": "neo4aas.eclass.spec_cd_writer",
    "derive_has_property_edges": "neo4aas.eclass.derive_edges",
    "derive_has_unit_edges": "neo4aas.eclass.derive_edges",
    "build_concept_descriptions": "neo4aas.eclass.ingest",
    "ingest_eclass": "neo4aas.eclass.ingest",
    "push_concept_descriptions": "neo4aas.eclass.ingest",
}


def __getattr__(name: str):
    """Resolve the basyx-dependent re-exports on demand."""
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))

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
    "derive_has_unit_edges",
]
