import os
import json
from typing import Union

from aas_mapping.aas_neo4j_adapter.querification.aasql_to_ast import parse_aasql_full
from pprint import pprint
from aas_mapping.aas_neo4j_adapter.querification.ast_to_cypher import converter_full

def convert_aasql_to_cypher(aasql_query: Union[dict, str]) -> str:
    if isinstance(aasql_query, str):
        aasql_query = json.loads(aasql_query)

    ast = parse_aasql_full(aasql_query)
    pprint(ast)
    cypher = converter_full(ast)
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
