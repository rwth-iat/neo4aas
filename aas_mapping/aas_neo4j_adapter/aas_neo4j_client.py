import logging
import re
from typing import Dict, List, Optional, Set, Tuple, Any
import json

from aas_mapping.aas_neo4j_adapter.base import Neo4jModelConfig
from aas_mapping.aas_neo4j_adapter.jsonification.neo4j_export import JsonFromNeo4jExporter
from aas_mapping.aas_neo4j_adapter.jsonification.neo4j_import import JsonToNeo4jImporter
from aas_mapping.aas_neo4j_adapter.utils import NEO4J_INTERNAL_NODE_KEYS
from aas_mapping.aas_neo4j_adapter.xmlification.neo4j_import import XmlToNeo4jImporter

# Configure logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

IDENTIFIABLE_KEYS = {
    "assetAdministrationShells": "AssetAdministrationShell",
    "submodels": "Submodel",
    "conceptDescriptions": "ConceptDescription",
}
AAS_CLS_PARENTS: dict[str, tuple[str]] = {
    'AssetAdministrationShell': ('Identifiable', 'Referable',),
    'ConceptDescription': ('Identifiable', 'Referable',),
    'Submodel': ('Identifiable', 'Referable', 'Qualifiable',),
    'Capability': ('SubmodelElement', 'Referable', 'Qualifiable',),
    'Entity': ('SubmodelElement', 'Referable', 'Qualifiable',),
    'BasicEventElement': ('EventElement', 'SubmodelElement', 'Referable', 'Qualifiable',),
    'Operation': ('SubmodelElement', 'Referable', 'Qualifiable',),
    'RelationshipElement': ('SubmodelElement', 'Referable', 'Qualifiable',),
    'AnnotatedRelationshipElement': ('RelationshipElement', 'SubmodelElement', 'Referable', 'Qualifiable',),
    'SubmodelElementCollection': ('SubmodelElement', 'Referable', 'Qualifiable',),
    'SubmodelElementList': ('SubmodelElement', 'Referable', 'Qualifiable',),
    'Blob': ('DataElement', 'SubmodelElement', 'Referable', 'Qualifiable',),
    'File': ('DataElement', 'SubmodelElement', 'Referable', 'Qualifiable',),
    'MultiLanguageProperty': ('DataElement', 'SubmodelElement', 'Referable', 'Qualifiable',),
    'Property': ('DataElement', 'SubmodelElement', 'Referable', 'Qualifiable',),
    'Range': ('DataElement', 'SubmodelElement', 'Referable', 'Qualifiable',),
    'ReferenceElement': ('DataElement', 'SubmodelElement', 'Referable', 'Qualifiable',),
    'DataSpecificationIec61360': ('DataSpecificationContent',),
}


AAS_NEO4J_MODEL_CONFIG = Neo4jModelConfig(
    keys_to_ignore=(),
    virtual_relationships=("child", "references"),

    default_optimization_clauses=[
        "CREATE INDEX FOR (r:Identifiable) ON (r.id);",
        "CREATE INDEX FOR (r:Referable) ON (r.idShort);",
        "CREATE INDEX rel_list_index FOR () - [r:value]-() ON (r.list_index);"
    ],
    # Node types whose instances are content-deduplicated by SHA256 hash of their properties.
    # When two nodes of a deduplicated type have identical properties, only one is created in
    # Neo4j and all relationships point to that single canonical node.
    deduplicated_object_types={
        "Reference",
        "ConceptDescription",
        # "Qualifier",           # not deduplicated: qualifiers are structurally identical but semantically distinct per element
        # "Extension",           # same reasoning as Qualifier
        # "EmbeddedDataSpecification"
    },
    # Node properties that are lists of dicts with only scalar values are stored as parallel
    # flat lists instead, since Neo4j does not support list-of-dict properties.
    # BEFORE: description = [{"language": "en", "text": "Foo"}, {"language": "de", "text": "Bar"}]
    # AFTER:  description_language = ["en", "de"]
    #         description_text     = ["Foo", "Bar"]
    list_of_dicts_prop_as_multiple_list_props={
        "Reference": ["keys"],
        "Referable": ["description", "displayName"],
        "MultiLanguageProperty": ["value"],
        "DataSpecificationIec61360": ["preferredName", "shortName", "definition"],
        # "Qualifiable": ["qualifiers"]  # excluded: Qualifier can itself contain a Reference (semanticId)
    },
    # Node properties that are flat dicts with only scalar values are inlined as prefixed
    # scalar properties, since Neo4j does not support dict-typed properties.
    # BEFORE: defaultThumbnail = {"path": "/img/thumb.png", "contentType": "image/png"}
    # AFTER:  defaultThumbnail_path        = "/img/thumb.png"
    #         defaultThumbnail_contentType = "image/png"
    # Only use this for dicts whose sub-fields are all scalars. Dicts that contain nested
    # objects or lists must be stored as child nodes via a relationship instead.
    dict_prop_as_multiple_props = {
        "AssetInformation": ["defaultThumbnail"],
        # "Reference": ["referredSemanticId"],  # excluded: referredSemanticId is itself a Reference with a keys list
        # "Identifiable": ["administration"],   # excluded: AdministrativeInformation can contain a Reference (creator)
    },
    all_list_item_relationships_have_index = False,
    list_item_relationships_with_index = {
        "SubmodelElementList": ["value"],
        "AssetInformation": ["specificAssetIds"],
        "HasSemantics": ["supplementalSemanticIds"],
    }
)


class AASNeo4JClient(XmlToNeo4jImporter, JsonFromNeo4jExporter):
    node_names: Set[str] = set()

    def _process_json_data(self, json_data: Dict[str, Any]) -> Tuple[List[Dict], Dict[str, List]]:
        """
        Process JSON data into nodes and relationships.

        This is an oveloaded method to process the AAS JSON Environment and skip upper keys
        """
        nodes = []
        relationships = {}

        for key, label in IDENTIFIABLE_KEYS.items():
            try:
                for obj in json_data[key]:
                    child_nodes, child_rels = self._process_dict(obj)
                    nodes.extend(child_nodes)
                    self._merge_relationships(relationships, child_rels)
            except KeyError:
                logger.info(f"Key '{key}' not found in the JSON file")
        return nodes, relationships


    @staticmethod
    def identify_labels(obj: Dict) -> Tuple[str]:
        """
        Return the types of the given object for neo4j labels

        This is an oveloaded method to return the AAS object types as labels
        """
        RELATIONSHIP_TYPES = ("ExternalReference", "ModelReference")
        QUALIFIER_KINDS = ("ValueQualifier", "ConceptQualifier", "TemplateQualifier")

        if "modelType" in obj:
            class_name = obj["modelType"]
            types = (class_name, *AAS_CLS_PARENTS[class_name])
            return types
        elif "type" in obj and obj["type"] in RELATIONSHIP_TYPES:
            return ("Reference",)
        elif "kind" in obj and obj["kind"] in QUALIFIER_KINDS:
            return ("Qualifier",)
        elif "language" in obj and "text" in obj:
            return ("LangString",)
        elif "assetKind" in obj:
            return ("AssetInformation",)
        elif "dataSpecification" in obj and "dataSpecificationContent" in obj:
            return ("EmbeddedDataSpecification",)
        else:
            return JsonToNeo4jImporter.identify_labels(obj)

    def add_identifiable(self, obj: Dict):
        if self.identifiable_exists(obj['id']):
            raise KeyError(f"Identifiable with id {obj['id']} already exists in the database.")
        nodes, relationships = self._process_dict(obj)
        return self._upload_nodes_and_relationships(nodes, relationships)

    def add_referable(self, obj: Dict, parent_id: Optional[str] = None, id_short_path: Optional[str] = None):
        node_labels = self.identify_labels(obj)
        if "Identifiable" in node_labels:
            if parent_id or id_short_path:
                raise ValueError("Parent ID or ID short path should not be provided for Identifiable objects")
            return self.add_identifiable(obj)
        else:
            if not (parent_id and id_short_path):
                raise ValueError("Parent ID and ID short path should be provided for Referable objects")
            return self.add_submodel_element(obj, parent_id, id_short_path)

    def add_submodel_element(self, obj: Dict, parent_id: str, id_short_path: str):
        parent_node_internal_id = self._find_node(parent_id, id_short_path)
        nodes, relationships = self._process_dict(obj)

        self._add_relationship(relationships, "child", parent_node_internal_id, nodes[-1]['uid'])
        self._add_relationship(relationships, "value", parent_node_internal_id, nodes[-1]['uid'])
        stats = self._upload_nodes_and_relationships(nodes, relationships,
                                                     exist_uid_to_internal_id={
                                                         parent_node_internal_id: parent_node_internal_id})
        return stats

    def identifiable_exists(self, identifier: str) -> bool:
        """Check if an Identifiable node with the given ID exists in the Neo4j database."""
        clause = f"MATCH (n:Identifiable {{id: '{identifier}'}} ) RETURN count(n)>0"
        result = self.execute_clause(clause, single=True)
        return result[0]

    def remove_referable(self, parent_id: str, id_short_path: str = None):
        clauses, referable_node = self._find_node_clause(parent_id, id_short_path)
        delete_clause = (
            f"CALL apoc.path.subgraphAll({referable_node}, {{relationshipFilter: '>'}}) YIELD nodes "
            "UNWIND nodes AS node "
            "WITH node, nodes WHERE NOT EXISTS { MATCH (other)-[]->(node) WHERE NOT other IN nodes } "
            "DETACH DELETE node "
            "RETURN count(node) AS deletedNodes; "
        )
        return self.execute_clause(clauses + delete_clause)

    def remove_identifiable(self, identifier: str):
        return self.remove_referable(identifier)

    @staticmethod
    def _strip_internal_keys(value):
        """Recursively remove Neo4j-internal node properties (uid, hash) from exported dicts."""
        if isinstance(value, dict):
            return {k: AASNeo4JClient._strip_internal_keys(v) for k, v in value.items() if k not in NEO4J_INTERNAL_NODE_KEYS}
        if isinstance(value, list):
            return [AASNeo4JClient._strip_internal_keys(item) for item in value]
        return value

    def get_referable(self, parent_id: str, id_short_path: str = None) -> Dict:
        subgraph_json = self._get_subgraph_of_referable(parent_id, id_short_path)
        return self._strip_internal_keys(self.convert_subgraph_to_data_dict(subgraph_json))

    def get_identifiable(self, identifier: str) -> Dict:
        return self.get_referable(identifier)

    def count_nodes_with_label(self, label: str) -> int:
        """Count the number of nodes with a specific label."""
        clause = f"MATCH (n:{label}) RETURN COUNT(n) AS count"
        result = self.execute_clause(clause, single=True)
        return result["count"] if result else 0

    def count_referables(self) -> int:
        return self.count_nodes_with_label("Referable")

    def count_identifiables(self) -> int:
        return self.count_nodes_with_label("Identifiable")

    def resolve_references(self) -> int:
        """Maintain :references edges from every ModelReference to the Referable it targets.

        A ModelReference addresses its target by a chain of keys: ``keys_value[0]`` is the
        global ``id`` of an Identifiable (AAS / Submodel / ConceptDescription), and each
        subsequent key descends into a nested Referable. The mode of a descending key
        depends on the parent: it is an ``idShort`` under a SubmodelElementCollection /
        Submodel, but a 0-based list index under a SubmodelElementList. A single match
        pattern handles both — ``child.idShort = key OR edge.list_index = toInteger(key)``
        (``toInteger`` of an idShort is null, so only the right branch ever matches).

        All ``:references`` edges are dropped and rebuilt, so the result is idempotent and
        self-healing: retargeted, deleted, or re-identified targets leave no stale links,
        and a reference added before its target is linked once the target appears.
        Returns the number of references that resolved to a target.
        """
        self.execute_clause("MATCH (:Reference)-[rel:references]->() DELETE rel")

        refs = self.execute_clause(
            "MATCH (r:Reference {type: 'ModelReference'}) "
            "WHERE r.keys_value IS NOT NULL AND size(r.keys_value) > 0 "
            "RETURN id(r) AS rid, r.keys_value AS kv"
        ) or []

        resolved = 0
        for rec in refs:
            rid, kv = rec["rid"], rec["kv"]
            clause = "MATCH (r) WHERE id(r) = $rid\nMATCH (t0:Identifiable {id: $k0})"
            params = {"rid": rid, "k0": kv[0]}
            last = "t0"
            for i in range(1, len(kv)):
                nxt = f"t{i}"
                clause += (
                    f"\nMATCH ({last})-[e{i}]->({nxt}:Referable) "
                    f"WHERE {nxt}.idShort = $k{i} OR e{i}.list_index = toInteger($k{i})"
                )
                params[f"k{i}"] = kv[i]
                last = nxt
            clause += f"\nMERGE (r)-[:references]->({last})\nRETURN count(*) AS c"
            result = self.execute_clause(clause, single=True, params=params)
            if result and result["c"]:
                resolved += 1
        return resolved

    # Backwards-compatible alias for the create-only name.
    create_reference_relationships = resolve_references

    def _find_node(self, parent_id: str, id_short_path: Optional[str] = None) -> int:
        """
        Find a node in the Neo4j database based on the parent ID and optional idShortPath.
        """
        clause, found_node = self._find_node_clause(parent_id, id_short_path)
        clause += f"RETURN ID({found_node}) AS node_id"
        with self.driver.session() as session:
            result = session.run(clause).single()
            if result is None:
                raise KeyError(f"No node found with parent_id={parent_id} and id_short_path={id_short_path}")
            elif len(result["node_id"]) != 1:
                raise ValueError(f"Multiple nodes found with parent_id={parent_id} and id_short_path={id_short_path}")
            return result["node_id"][0]

    def _find_node_clause(self, parent_id: str, id_short_path: Optional[str] = None) -> (str, str):
        found_node = "the_node"

        if not id_short_path:
            return f"MATCH ({found_node}:Identifiable {{id: '{parent_id}'}})\n", found_node

        clause = f"MATCH (parent:Identifiable {{id: '{parent_id}'}})"
        id_shorts = self.itemize_id_short_path(id_short_path)
        for i, idShort in enumerate(id_shorts):
            if not i == len(id_shorts) - 1:
                clause += f"-[:child]->(child_{i} {{idShort: '{idShort}'}})\n"
            else:
                clause += f"-[:child]->({found_node} {{idShort: '{idShort}'}})\n"
        return clause, found_node

    def _get_subgraph_of_referable(self, parent_id: str, id_short_path: Optional[str] = None):
        """
        Fetches a subgraph of Referable object from Neo4j.

        It includes the object node itself and all its children being attributes of the object.
        """
        find_node_clause, found_parent_node = self._find_node_clause(parent_id, id_short_path)
        get_subgraph_clause = (
            f"CALL apoc.path.subgraphAll({found_parent_node}, {{relationshipFilter: '>'}}) YIELD nodes, relationships "
            "WITH nodes "
            "OPTIONAL MATCH (a)-[r]->(b) WHERE a IN nodes AND b IN nodes "
            "WITH nodes, collect(r) AS allRels "
            "RETURN apoc.convert.toJson({nodes: nodes, relationships: allRels}) AS json;"
        )
        result = self.execute_clause(find_node_clause + get_subgraph_clause, single=True)
        if result is None:
            raise KeyError(f"No Referable found with: id={parent_id}, id_short_path={id_short_path}")
        subgraph_json = json.loads(result["json"])
        return subgraph_json

    @staticmethod
    def itemize_id_short_path(id_short_path: str) -> List[str]:
        """
        Split the idShortPath into a list of idShorts. Dot separated or brackets with index.

        Example Input: "MySubmodelElementCollection.MySubSubmodelElementList2[0][0].MySubTestValue3"
        Example Result: ["MySubmodelElementCollection", "MySubSubmodelElementList2", 0, 0, "MySubTestValue3"]
        :param idShortPath: The path to the idShort attribute.
        """
        pattern = r'([a-zA-Z_]\w*)|\[(\d+)\]'
        matches = re.findall(pattern, id_short_path)
        result = [match[0] if match[0] else int(match[1]) for match in matches]
        return result


def main():
    def optimized_upload_all_submodels(submodels_folder="../examples/aas/test_dataset/"):
        # Use the OptimizedAASNeo4JClient for batch processing
        optimized_client = AASNeo4JClient(uri="bolt://localhost:7687", user="neo4j", password="12345678",
                                          model_config=AAS_NEO4J_MODEL_CONFIG)
        optimized_client._remove_all()
        optimized_client._remove_all_indexes_and_constraints()
        optimized_client.optimize_database()
        # set timer
        import time
        start_time = time.time()
        result = optimized_client.upload_all_json_from_dir(submodels_folder,
                                                           file_batch_size=100,
                                                           db_batch_size=30000,
                                                           max_num_of_batches=1)
        end_time = time.time()
        print(f"Execution time: {end_time - start_time} seconds")

    def get_sm_from_neo4j():
        client = AASNeo4JClient(uri="bolt://localhost:7687", user="neo4j", password="12345678")
        sm = client.get_identifiable('https://smart.festo.com/sm/004/2dcd48b2-88a5-463a-9396-deaece98b4c9/')
        print(sm)


    logger.setLevel(logging.INFO)
    optimized_upload_all_submodels() # submodels_folder="examples/submodels/")


if __name__ == '__main__':
    main()
