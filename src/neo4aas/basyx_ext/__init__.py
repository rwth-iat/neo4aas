"""basyx-python-sdk integration for neo4aas.

Everything in the project that imports ``basyx`` lives here (plus
:mod:`neo4aas.eclass`, which builds ConceptDescription objects): the
:class:`~neo4aas.basyx_ext.object_store.Neo4jObjectStore` bridge implementing the
SDK's ``AbstractObjectStore``, and the Repository server that consumes it.

Requires the ``basyx`` extra: ``pip install "neo4aas[basyx]"``.
"""

from neo4aas.basyx_ext.object_store import Neo4jObjectStore

__all__ = ["Neo4jObjectStore"]
