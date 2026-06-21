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
        # Uniqueness constraint enforces a single node per Identifiable id (per the AAS spec)
        # and provides the backing index for id lookups in one rule.
        "CREATE CONSTRAINT identifiable_id IF NOT EXISTS FOR (r:Identifiable) REQUIRE r.id IS UNIQUE;",
        "CREATE INDEX referable_idshort IF NOT EXISTS FOR (r:Referable) ON (r.idShort);",
        "CREATE INDEX rel_list_index IF NOT EXISTS FOR () - [r:value]-() ON (r.list_index);",
        # Indexed lookup of "references targeting id X" for incremental resolution.
        "CREATE INDEX reference_target_id IF NOT EXISTS FOR (r:Reference) ON (r.target_id);",
        # Backing hash indexes for cross-import deduplication are derived from
        # `deduplicated_object_types` in optimize_database(), so they always match the config.
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

        # The new element is a list member of the parent's `value`, so the edge must carry
        # `is_list` (and, for a SubmodelElementList parent, the positional `list_index`) — the
        # same tagging the bulk import applies. Without it, export reconstructs the element as
        # a scalar `value` and loses list membership/order.
        parent_info = self.execute_clause(
            "MATCH (p) WHERE elementId(p) = $pid "
            "OPTIONAL MATCH (p)-[:value]->(c) "
            "RETURN 'SubmodelElementList' IN labels(p) AS is_list, count(c) AS n",
            single=True,
            params={"pid": parent_node_internal_id},
        )
        rel_props = {"is_list": True}
        if parent_info and parent_info["is_list"]:
            rel_props["list_index"] = parent_info["n"]
        self._add_relationship(relationships, "value", parent_node_internal_id, nodes[-1]['uid'],
                               rel_props=rel_props)
        stats = self._upload_nodes_and_relationships(nodes, relationships,
                                                     exist_uid_to_internal_id={
                                                         parent_node_internal_id: parent_node_internal_id})
        # A newly added SME may contain a ModelReference; resolve edges for the owning shell/submodel.
        self.resolve_references_for(parent_id)
        return stats

    def identifiable_exists(self, identifier: str) -> bool:
        """Check if an Identifiable node with the given ID exists in the Neo4j database."""
        clause = "MATCH (n:Identifiable {id: $id}) RETURN count(n)>0"
        result = self.execute_clause(clause, single=True, params={"id": identifier})
        return result[0]

    def remove_referable(self, parent_id: str, id_short_path: str = None):
        # Resolve to exactly one node via _find_node, which raises if the path matches zero
        # (KeyError) or more than one (ValueError). This prevents an unbounded DETACH DELETE
        # of several subtrees when a path is malformed or hits a spec-violating duplicate idShort.
        root_internal_id = self._find_node(parent_id, id_short_path)
        # Delete the target referable and every node in its owned subtree, but keep nodes
        # that are still referenced from outside the subtree (e.g. deduplicated References /
        # ConceptDescriptions shared by other elements). The target root itself is always
        # deleted even though its container points at it from outside the subtree.
        delete_clause = (
            "MATCH (root) WHERE elementId(root) = $rid "
            "CALL apoc.path.subgraphAll(root, {relationshipFilter: '>'}) YIELD nodes "
            "UNWIND nodes AS node "
            "WITH root, node, nodes "
            "WHERE node = root OR NOT EXISTS { MATCH (other)-[]->(node) WHERE NOT other IN nodes } "
            "DETACH DELETE node "
            "RETURN count(node) AS deletedNodes; "
        )
        return self.execute_clause(delete_clause, params={"rid": root_internal_id})

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

    # Shared fragments for the resolvers: a ModelReference `r` is resolvable when it has a
    # non-empty keys_value chain; the resolvers consume (rid, kv) records.
    _MODELREF_COND = "r.keys_value IS NOT NULL AND size(r.keys_value) > 0"
    _MODELREF_RETURN = "RETURN id(r) AS rid, r.keys_value AS kv"

    def _resolve_refs(self, refs: list) -> int:
        """(Re)build the ``:references`` edge for each given reference.

        A ModelReference addresses its target by a chain of keys: ``keys_value[0]`` is the
        global ``id`` of an Identifiable (AAS / Submodel / ConceptDescription), and each
        subsequent key descends into a nested Referable. The mode of a descending key
        depends on the parent: an ``idShort`` under a SubmodelElementCollection / Submodel,
        a 0-based list index under a SubmodelElementList. One match pattern handles both —
        ``child.idShort = key OR edge.list_index = toInteger(key)`` (``toInteger`` of an
        idShort is null, so only the right branch ever matches).

        Each reference's existing ``:references`` edge is dropped first, so re-resolving a
        reference is idempotent and self-healing. ``refs`` items are ``{rid, kv}`` records.
        Returns the number of references that resolved to a target.
        """
        resolved = 0
        for rec in refs:
            rid, kv = rec["rid"], rec["kv"]
            self.execute_clause(
                "MATCH (r)-[rel:references]->() WHERE id(r) = $rid DELETE rel",
                params={"rid": rid},
            )
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

    def resolve_references(self) -> int:
        """Rebuild every ``:references`` edge across the whole graph (idempotent).

        Drops all ``:references`` edges, then resolves every ModelReference. Use after a
        bulk import; single-object writes via :class:`Neo4jObjectStore` use the incremental
        :meth:`resolve_references_for`. Returns the number of references resolved.
        """
        self.execute_clause("MATCH (:Reference)-[rel:references]->() DELETE rel")
        refs = self.execute_clause(
            f"MATCH (r:Reference {{type: 'ModelReference'}}) "
            f"WHERE {self._MODELREF_COND} {self._MODELREF_RETURN}"
        ) or []
        return self._resolve_refs(refs)

    def resolve_references_for(self, identifier: str) -> int:
        """Incrementally (re)resolve only the references affected by adding ``identifier``.

        Two reference sets are affected when an Identifiable appears:
        - references **inside** its subgraph (newly imported references), and
        - references **targeting** it (``target_id == identifier``) which may now resolve
          into its freshly-available subtree — found by an indexed lookup.

        Removing an Identifiable needs no work here: ``DETACH DELETE`` drops every
        ``:references`` edge pointing into the deleted subtree. Returns refs resolved.
        """
        in_subtree = self.execute_clause(
            "MATCH (root:Identifiable {id: $id}) "
            "CALL apoc.path.subgraphAll(root, {relationshipFilter: '>'}) YIELD nodes "
            "UNWIND nodes AS r "
            f"WITH r WHERE r:Reference AND r.type = 'ModelReference' AND {self._MODELREF_COND} "
            f"{self._MODELREF_RETURN}",
            params={"id": identifier},
        ) or []
        targeting = self.execute_clause(
            f"MATCH (r:Reference {{target_id: $id, type: 'ModelReference'}}) "
            f"WHERE {self._MODELREF_COND} {self._MODELREF_RETURN}",
            params={"id": identifier},
        ) or []
        by_rid = {rec["rid"]: rec for rec in (*in_subtree, *targeting)}
        return self._resolve_refs(list(by_rid.values()))

    # Backwards-compatible alias for the create-only name.
    create_reference_relationships = resolve_references

    def _find_node(self, parent_id: str, id_short_path: Optional[str] = None) -> str:
        """
        Find a node in the Neo4j database based on the parent ID and optional idShortPath.

        Returns the node's ``elementId`` (matching what the import path uses to wire
        relationships).
        """
        clause, found_node, params = self._find_node_clause(parent_id, id_short_path)
        clause += f"RETURN collect(DISTINCT elementId({found_node})) AS node_ids"
        with self.driver.session() as session:
            result = session.run(clause, **params).single()
        node_ids = result["node_ids"] if result else []
        if not node_ids:
            raise KeyError(f"No node found with parent_id={parent_id} and id_short_path={id_short_path}")
        if len(node_ids) != 1:
            raise ValueError(f"Multiple nodes found with parent_id={parent_id} and id_short_path={id_short_path}")
        return node_ids[0]

    def _find_node_clause(self, parent_id: str, id_short_path: Optional[str] = None) -> (str, str, dict):
        """Build a MATCH clause locating a node by id (+ optional idShort path).

        Returns ``(clause, found_node_var, params)``. All caller-supplied values (the
        Identifier and each idShort/index segment) are emitted as ``$`` parameters, so the
        clause is injection-safe; callers must pass ``params`` to ``execute_clause`` /
        ``session.run``.
        """
        found_node = "the_node"

        if not id_short_path:
            return f"MATCH ({found_node}:Identifiable {{id: $parent_id}})\n", found_node, {"parent_id": parent_id}

        # Traverse containment by following the semantic edges (no :child edge). Each step
        # descends to a Referable child matched either by idShort (Collection / Submodel
        # member) or, for a SubmodelElementList member, by the edge's list_index.
        clause = "MATCH (parent:Identifiable {id: $parent_id})\n"
        params: dict = {"parent_id": parent_id}
        segments = self.itemize_id_short_path(id_short_path)
        prev = "parent"
        wheres = []
        for i, seg in enumerate(segments):
            var = found_node if i == len(segments) - 1 else f"child_{i}"
            clause += f"MATCH ({prev})-[e_{i}]->({var}:Referable)\n"
            params[f"seg_{i}"] = seg
            if isinstance(seg, int):
                wheres.append(f"e_{i}.list_index = $seg_{i}")
            else:
                wheres.append(f"{var}.idShort = $seg_{i}")
            prev = var
        if wheres:
            clause += "WHERE " + " AND ".join(wheres) + "\n"
        return clause, found_node, params

    def _get_subgraph_of_referable(self, parent_id: str, id_short_path: Optional[str] = None):
        """
        Fetches a subgraph of Referable object from Neo4j.

        It includes the object node itself and all its children being attributes of the object.
        """
        find_node_clause, found_parent_node, params = self._find_node_clause(parent_id, id_short_path)
        get_subgraph_clause = (
            f"CALL apoc.path.subgraphAll({found_parent_node}, {{relationshipFilter: '>'}}) YIELD nodes, relationships "
            "WITH nodes "
            "OPTIONAL MATCH (a)-[r]->(b) WHERE a IN nodes AND b IN nodes "
            "WITH nodes, collect(r) AS allRels "
            "RETURN apoc.convert.toJson({nodes: nodes, relationships: allRels}) AS json;"
        )
        result = self.execute_clause(find_node_clause + get_subgraph_clause, single=True, params=params)
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
