"""Repository WSGI app specialized for the Neo4j backend.

The stock basyx repository lists shells/submodels/concept-descriptions by iterating the
*entire* object store and filtering by ``isinstance`` (``_get_all_obj_of_type``). With a
large ECLASS ConceptDescription dictionary loaded that materializes 100k+ full objects per
list request and OOM-kills the worker. This subclass overrides the single listing chokepoint
to query Neo4j *by type, lazily*, so each list endpoint touches only its own nodes and the
server's pagination (``itertools.islice``) materializes just one page.

This module is imported only in the repository entrypoint (which already depends on the
basyx ``app`` server package); the core ``neo4aas`` library does not import it.
"""

import basyx.aas.model as model
from app.interfaces.repository import WSGIApp

from neo4aas.basyx_ext.object_store import Neo4jObjectStore


class Neo4jWSGIApp(WSGIApp):
    """``WSGIApp`` that lists by typed Neo4j queries instead of full store iteration."""

    # Model type -> Neo4j node label (our nodes carry the full class hierarchy as labels).
    _TYPE_LABELS = {
        model.AssetAdministrationShell: "AssetAdministrationShell",
        model.Submodel: "Submodel",
        model.ConceptDescription: "ConceptDescription",
    }

    def _get_all_obj_of_type(self, type_):
        label = self._TYPE_LABELS.get(type_)
        store = self.object_store
        if label is not None and isinstance(store, Neo4jObjectStore):
            return store.iter_by_label(label)
        # Any other type or store: fall back to the generic full-iteration filter.
        return super()._get_all_obj_of_type(type_)
