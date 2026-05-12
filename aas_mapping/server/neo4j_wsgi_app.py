import json
from typing import Dict

import werkzeug.exceptions
from werkzeug import Request, Response
from werkzeug.exceptions import BadRequest
from werkzeug.routing import Rule

from app.interfaces.repository import WSGIApp
from app.model import ServiceSpecificationProfileEnum, ServiceDescription

_NEO4J_SUPPORTED_PROFILES = ServiceDescription([
    ServiceSpecificationProfileEnum.AAS_REPOSITORY_FULL,
    ServiceSpecificationProfileEnum.SUBMODEL_REPOSITORY_FULL,
    ServiceSpecificationProfileEnum.AAS_REPOSITORY_READ,
    ServiceSpecificationProfileEnum.SUBMODEL_REPOSITORY_READ,
    ServiceSpecificationProfileEnum.AAS_REPOSITORY_QUERY,
    ServiceSpecificationProfileEnum.SUBMODEL_REPOSITORY_QUERY,
])


class Neo4jWSGIApp(WSGIApp):
    """WSGIApp subclass that adds AASQL /query/shells and /query/submodels endpoints."""

    def __init__(self, object_store, file_store, base_path: str = "/api/v3.1"):
        super().__init__(object_store, file_store, base_path)
        self.url_map.add(Rule(f"{base_path}/query/shells", methods=["POST"], endpoint=self.query_shells))
        self.url_map.add(Rule(f"{base_path}/query/submodels", methods=["POST"], endpoint=self.query_submodels))
        self.url_map.update()

    def get_description(self, request: Request, url_args: Dict, **kwargs) -> Response:
        from app.interfaces.base import APIResponse
        response_t = kwargs.get("response_t", APIResponse)
        return response_t(_NEO4J_SUPPORTED_PROFILES.to_dict())

    def _query_neo4j(self, request: Request, return_var: str) -> list:
        from aas_mapping.aas_neo4j_adapter.neo_aas_object_store import Neo4jObjectStore
        from aas_mapping.aas_neo4j_adapter.querification.aasql_to_cypher import convert_aasql_to_cypher

        if not isinstance(self.object_store, Neo4jObjectStore):
            raise werkzeug.exceptions.NotImplemented(
                "Query endpoints require Neo4j backend (set STORAGE_BACKEND=neo4j)"
            )

        client = self.object_store._client
        try:
            cypher = convert_aasql_to_cypher(request.get_data(as_text=True))
        except (json.JSONDecodeError, ValueError) as e:
            raise BadRequest(f"Invalid AASQL query: {e}") from e

        records = client.execute_clause(cypher) or []
        results = []
        for record in records:
            if return_var in record.keys():
                obj_id = record[return_var]["id"]
            elif f"{return_var}.id" in record.keys():
                obj_id = record[f"{return_var}.id"]
            else:
                continue
            results.append(client.get_identifiable(obj_id))
        return results

    def query_submodels(self, request: Request, url_args: Dict, **_kwargs) -> Response:
        results = self._query_neo4j(request, "sm")
        return Response(
            json.dumps({"paging_metadata": {"resultType": "Submodel"}, "result": results}),
            content_type="application/json",
        )

    def query_shells(self, request: Request, url_args: Dict, **_kwargs) -> Response:
        results = self._query_neo4j(request, "aas")
        return Response(
            json.dumps({"paging_metadata": {"resultType": "AssetAdministrationShell"}, "result": results}),
            content_type="application/json",
        )
