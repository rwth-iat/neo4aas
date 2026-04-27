import os
import json
from typing import Optional, Union

from aas_mapping.aas_neo4j_adapter.base import Neo4jModelConfig
from aas_mapping.aas_neo4j_adapter.querification.aasql_to_ast import parse_aasql_query
from aas_mapping.aas_neo4j_adapter.querification.ast_to_cypher import converter


def convert_aasql_to_cypher(
    aasql_query: Union[dict, str],
    config: Optional[Neo4jModelConfig] = None,
) -> str:
    """
    Convert an AASQL JSON query to a Cypher query string.

    Args:
        aasql_query: AASQL query as a dict or JSON string.
        config: Optional ``Neo4jModelConfig``.  When provided, flattened
                property names are validated against
                ``list_of_dicts_prop_as_multiple_list_props``.
    """
    if isinstance(aasql_query, str):
        aasql_query = json.loads(aasql_query)

    ast = parse_aasql_query(aasql_query)
    cypher = converter(ast, config=config)
    return cypher

def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    query_dir = os.path.join(project_root, "aas_mapping", "examples", "queries")
    for file_name in os.listdir(query_dir):
        if file_name.endswith(".json"):
            path = os.path.join(query_dir, file_name)
            with open(path, "r") as f:
                data = json.load(f)
            print(f"--- {file_name} ---")
            cypher = convert_aasql_to_cypher(data)
            print(cypher)
            print("\n------------------------------")

if __name__ == "__main__":
    main()
