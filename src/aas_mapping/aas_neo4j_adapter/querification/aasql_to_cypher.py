import os
import json
from typing import Optional, Union

from aas_mapping.aas_neo4j_adapter.querification.aasql_to_ast import parse_aasql_full
from aas_mapping.aas_neo4j_adapter.querification.ast_to_cypher import converter_full

def convert_aasql_to_cypher(aasql_query: Union[dict, str], target: Optional[str] = None,
                            model_config=None) -> str:
    """Compile an AASQL query (dict or JSON string) to Cypher.

    ``target`` optionally forces the RETURN variable to the endpoint's object type
    (e.g. ``"aas"`` at an AAS repository, ``"sm"`` at a Submodel repository). When
    omitted, the outermost root present is returned (precedence ``aas > sm > cd``).

    ``model_config`` optionally supplies the ``Neo4jModelConfig`` used to resolve
    flattened property names (e.g. ``value_text``, ``keys_value``); defaults to the
    AAS config when omitted, so the flattened names follow config rather than literals.
    """
    if isinstance(aasql_query, str):
        aasql_query = json.loads(aasql_query)

    ast = parse_aasql_full(aasql_query)
    cypher = converter_full(ast, target=target, model_config=model_config)
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
