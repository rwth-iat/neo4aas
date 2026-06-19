import pytest

from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient
from aas_mapping.aas_neo4j_adapter.jsonification.neo4j_export import JsonFromNeo4jExporter


def _exporter():
    # uri=None -> no driver created; these methods are pure dict transforms.
    return JsonFromNeo4jExporter(uri=None, user="", password=None)


def _list_rel(end_id, list_index):
    return {
        "type": "value",
        "label": "value",
        "start": {"id": "root"},
        "end": {"id": end_id},
        "properties": {"is_list": True, "list_index": list_index},
    }


def test_sort_relationships_based_on_type_and_list_entries_by_index():
    """List relationships must be ordered by their `list_index` property.

    Regression test: the secondary sort key read `properties.value.list_index`
    (a nonexistent path) instead of `properties.list_index`, so relationships
    were never ordered by index and list children were reconstructed in the
    arbitrary order Neo4j returned them.
    """
    # Deliberately out of list_index order: 2, 0, 1
    relationships = [_list_rel("c2", 2), _list_rel("c0", 0), _list_rel("c1", 1)]

    sorted_rels = _exporter()._sort_relationships_based_on_type_and_list_entries_by_index(relationships)

    assert [rel["properties"]["list_index"] for rel in sorted_rels] == [0, 1, 2]


@pytest.mark.integration
def test_submodel_element_list_order_preserved(aas_client: AASNeo4JClient):
    """SubmodelElementList children must round-trip in their original order.

    End-to-end regression test for the export sort bug. Asserts order explicitly
    (no order-insensitive normalisation), unlike the generic round-trip tests
    which sort lists of dicts before comparing.
    """
    sm_id = "https://example.com/sm/sme-list-order"
    expected_values = ["item0", "item1", "item2", "item3", "item4"]
    env = {
        "submodels": [
            {
                "modelType": "Submodel",
                "id": sm_id,
                "idShort": "OrderedListSubmodel",
                "submodelElements": [
                    {
                        "modelType": "SubmodelElementList",
                        "idShort": "myList",
                        "orderRelevant": True,
                        "typeValueListElement": "Property",
                        "valueTypeListElement": "xs:string",
                        "value": [
                            {"modelType": "Property", "valueType": "xs:string", "value": v}
                            for v in expected_values
                        ],
                    }
                ],
            }
        ]
    }

    aas_client.upload_json(env)

    # Scramble the creation order of the list's `value` relationships while keeping
    # their `list_index` properties intact. Neo4j returns relationships in creation
    # order, so without this the subgraph query happens to hand them back already
    # sorted — which masks the export sort bug. Recreating them in reverse forces
    # the exporter to rely on `list_index` for ordering.
    scramble = (
        "MATCH (p:SubmodelElementList {idShort: 'myList'})-[r:value]->(c) "
        "WITH p, r, c, properties(r) AS props ORDER BY props.list_index DESC "
        "DELETE r "
        "WITH p, collect({c: c, props: props}) AS items "
        "UNWIND items AS it "
        "CALL apoc.create.relationship(p, 'value', it.props, it.c) YIELD rel "
        "RETURN count(rel) AS n"
    )
    aas_client.execute_clause(scramble)

    retrieved = aas_client.get_identifiable(sm_id)

    sme_list = next(e for e in retrieved["submodelElements"] if e["idShort"] == "myList")
    assert [item["value"] for item in sme_list["value"]] == expected_values
