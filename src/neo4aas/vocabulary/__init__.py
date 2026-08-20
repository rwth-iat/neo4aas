"""The vocabulary graph — what an AAS corpus *says*, never what it contains.

This package is a second, independent importer that sits beside the instance
importer in :mod:`neo4aas.core.serialization`. It shares nothing with it but the
neo4j driver, and it obeys one rule:

    **No instance ever becomes a node.**

No element nodes, no per-submodel subtrees, no ``:Reference`` node per occurrence.
The instance importer does that, and it is why it does not scale: the reference
implementation landed 39 930 ``:semanticId`` edges on 68 nodes, which at 300 000
shells is a single node referenced 10–20 million times.

What is stored instead is the corpus's *vocabulary* — the distinct things:

===============  ====================================================  ==========
node             is one distinct …                                      measured
===============  ====================================================  ==========
``:Shape``       ``(idShort, semanticId, modelType, valueType)``         4 686
``:Structure``   ordered shape sequence of a submodel                    1 185
``:SemanticId``  declared semantic identifier, resolved or not           3 115
``:Concept``     dictionary entry (ECLASS, IEC CDD, embedded CD)       ~350 000
``:Template``    published/deprecated IDTA submodel template                58
``:Vendor``      corpus subdirectory                                          8
===============  ====================================================  ==========

Those counts come from 3 000 real shells / 860 260 elements
(``discaas/docs/findings/13-redundancy-and-the-storage-unit.md`` §10). They are
what the graph holds at **any** corpus size: 300× shape reuse, 8.5× structure
reuse, 543× Reference reuse. If a node count tracks the number of ingested files,
instances have leaked in and the design is broken.

Modules
-------

``extract``    documents → shape/structure/vendor records. Pure: no driver.
``concepts``   ECLASS OntoML + AAS-embedded ConceptDescriptions → ``:Concept``.
``templates``  IDTA submodel templates → ``:Template`` + ``:DECLARES_SHAPE``.
``schema``     the uniqueness constraints every ``MERGE`` needs to not be a scan.
``load``       batched ``MERGE`` writes. Never ``CREATE``.
``report``     the run report: counts by label, resolution rate, failures.
``cli``        ``neo4aas vocab import|stats|clear``.
"""

from neo4aas.vocabulary.extract import (
    Shape,
    ShapeUse,
    Slot,
    Structure,
    VocabularyRecords,
    extract_paths,
    shape_key,
    structure_key,
)

__all__ = [
    "Shape",
    "ShapeUse",
    "Slot",
    "Structure",
    "VocabularyRecords",
    "extract_paths",
    "shape_key",
    "structure_key",
]
