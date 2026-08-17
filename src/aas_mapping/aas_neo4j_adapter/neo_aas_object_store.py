import json
from typing import Iterator, Iterable

from basyx.aas.adapter.json import AASToJsonEncoder, StrictAASFromJsonDecoder
from basyx.aas.model import AbstractObjectStore, Identifiable, Identifier

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient


class Neo4jObjectStore(AbstractObjectStore[Identifier, Identifiable]):
    """
    A Neo4j object store that extends the AbstractObjectStore and uses a Neo4j database as the backend.
    It uses the AASNeo4JClient to interact with the Neo4j database.
    """
    def __init__(self, client: AASNeo4JClient, objects: Iterable[Identifiable] = ()) -> None:
        self._client: AASNeo4JClient = client
        for x in objects:
            self.add(x)

    def add(self, x: Identifiable) -> None:
        if self._client.identifiable_exists(x.id):
            raise KeyError(f"Identifiable object with same id {x.id} is already stored in this store")
        data = json.dumps(obj=x, cls=AASToJsonEncoder)
        data_dict = json.loads(data)
        self._client.add_identifiable(data_dict)
        self._client.resolve_references_for(x.id)

    def get_item(self, identifier: Identifier) -> Identifiable:
        return self.get_identifiable(identifier)

    def get_identifiable(self, identifier: Identifier) -> Identifiable:
        try:
            data = self._client.get_identifiable(identifier)
        except KeyError:
            raise KeyError(identifier)
        obj = json.loads(json.dumps(data), cls=StrictAASFromJsonDecoder)
        return obj

    def commit(self, x: Identifiable) -> None:
        if not self._client.identifiable_exists(x.id):
            raise KeyError(f"Identifiable object with id {x.id} not found in Neo4j store")
        self._client.remove_identifiable(x.id)
        data = json.dumps(obj=x, cls=AASToJsonEncoder)
        data_dict = json.loads(data)
        self._client.add_identifiable(data_dict)
        self._client.resolve_references_for(x.id)

    def discard(self, x: Identifiable) -> None:
        # DETACH DELETE drops every :references edge pointing into the removed subtree,
        # so no re-resolution is needed. Silent when the object is absent (set.discard semantics).
        if self._client.identifiable_exists(x.id):
            self._client.remove_identifiable(x.id)

    def remove(self, x: Identifiable) -> None:
        if not self._client.identifiable_exists(x.id):
            raise KeyError(f"Identifiable object with id {x.id} not found in Neo4j store")

        result = self._client.remove_identifiable(x.id)
        if result == 0:
            raise KeyError(f"The Identifiable could not be removed: {x.id}")

    def __contains__(self, x: object) -> bool:
        if isinstance(x, Identifier):
            return self._client.identifiable_exists(x)
        elif isinstance(x, Identifiable):
            return self._client.identifiable_exists(x.id)
        return False

    def __len__(self):
        return self._client.count_identifiables()

    def __iter__(self) -> Iterator[Identifiable]:
        clause = "MATCH (r:Identifiable) RETURN r.id AS id"
        result = self._client.execute_clause(clause)
        for record in result:
            yield self.get_identifiable(record["id"])

    def iter_by_label(self, label: str) -> Iterator[Identifiable]:
        """Lazily yield Identifiables carrying a given Neo4j label (e.g. ``"Submodel"``).

        Only the ids are fetched up front; each full object is built per-yield, so a
        paginated consumer (``itertools.islice``) materializes just one page. Used by
        ``Neo4jWSGIApp`` to list a single type without iterating the whole store.
        ``label`` is an internal constant (a model type name), never user input.
        """
        clause = f"MATCH (r:`{label}`) RETURN r.id AS id"
        for record in self._client.execute_clause(clause):
            yield self.get_identifiable(record["id"])

    def query(self, aasql_body: str, return_var: str) -> list[dict]:
        """Execute an AASQL query and return matching serialized AAS objects.

        Satisfies the ``QueryableObjectStore`` protocol defined in the server layer.

        :param aasql_body: raw AASQL JSON string
        :param return_var: Cypher return variable (``"sm"`` for submodels, ``"aas"`` for shells)
        :return: list of serialized AAS/Submodel dicts
        """
        from aas_mapping.aas_neo4j_adapter.querification.aasql_to_cypher import convert_aasql_to_cypher
        cypher = convert_aasql_to_cypher(aasql_body)
        records = self._client.execute_clause(cypher) or []
        results = []
        for record in records:
            if return_var in record.keys():
                obj_id = record[return_var]["id"]
            elif f"{return_var}.id" in record.keys():
                obj_id = record[f"{return_var}.id"]
            else:
                continue
            results.append(self._client.get_identifiable(obj_id))
        return results
