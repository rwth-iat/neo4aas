import hashlib
import json
import logging
import os
import time
from os.path import join, isfile
from typing import Optional, List, Dict, Tuple, Any

from neo4j import Session
from neo4j.exceptions import ClientError

from neo4aas.core.base import BaseNeo4JClient, Neo4jModelConfig
from neo4aas.core.utils import UploadStats, irdi_base

logger = logging.getLogger(__name__)

class JsonToNeo4jImporter(BaseNeo4JClient):
    def __init__(self, uri: str, user: str , password: Optional[str] = None, model_config: Neo4jModelConfig = None,
                 **kwargs):
        # Forward any extra BaseNeo4JClient args (e.g. fix_on_import) down the MRO.
        super().__init__(uri, user, password, model_config, **kwargs)
        self.uid_counter = 0

        # e.g. {HASH: uid}
        self.deduplicated_nodes: dict[str, int] = {}
        self.deduplicated_to_existing_uid_map: dict[int, int] = {}
        self.deduplicated_rels: set[str] = set()
        self.uid_to_internal_id: dict[str, int] = {}

    def _gen_unique_node_name(self) -> int:
        self.uid_counter += 1
        return self.uid_counter

    def _group_nodes_by_label(self, nodes: List[Dict]) -> Dict[Tuple[str], List[Dict]]:
        """Group nodes by their (sorted) label tuple.

        Each bucket holds a shallow copy of the node without the ``labels`` key: labels
        become Neo4j node *labels* (via ``apoc.create.node``), not properties, so they must
        not reach the property bag. Copying (instead of ``pop``) keeps the input dicts intact
        so the caller's ``nodes`` list stays reusable and is never mutated as a side effect.
        """
        grouped = {}
        for node in nodes:
            labels = tuple(sorted(node['labels']))
            grouped.setdefault(labels, []).append({k: v for k, v in node.items() if k != 'labels'})
        return grouped

    def _add_relationship(self, relationships: Dict[str, List], rel_type: str, from_uid: int, to_uid: int,
                          rel_props: Optional[dict] = None):
        """Add a relationship to the relationships dictionary."""
        if rel_type not in relationships:
            relationships[rel_type] = []
        if rel_props is None:
            rel_props = {}
        relationships[rel_type].append({
            'from_uid': from_uid,
            'to_uid': to_uid,
            'rel_props': rel_props
        })

    def _merge_relationships(self, target: Dict[str, List], source: Dict[str, List]):
        """Merge relationships from source into target."""
        for key, value in source.items():
            target.setdefault(key, []).extend(value)

    def _create_nodes(self, session: Session, grouped_nodes: Dict[Tuple[str], List[Dict]]) -> Dict[int, int]:
        """Create nodes in Neo4j and return uid to internal_id mapping.

        Nodes of a deduplicated type carry a ``hash`` property (assigned in
        ``_deduplicate_nodes``) and are MERGEd on it, so an identical node imported by a
        different client / process reuses the canonical node already in the database
        instead of creating a duplicate. All other nodes are created as before.
        """
        create_nodes_query = """
        UNWIND keys($data) AS labelsString

        WITH split(labelsString, ",") AS labels, $data[labelsString] AS nodesProperties

        UNWIND nodesProperties AS nodeProperties
        CALL apoc.create.node(labels, nodeProperties) YIELD node AS n

        RETURN elementId(n) AS internal_id, nodeProperties.uid AS uid
        """
        # MERGE deduplicated nodes on their content hash so repeated imports of the same
        # Reference / ConceptDescription converge to a single node across client instances.
        merge_nodes_query = """
        UNWIND keys($data) AS labelsString

        WITH split(labelsString, ",") AS labels, $data[labelsString] AS nodesProperties

        UNWIND nodesProperties AS nodeProperties
        CALL apoc.merge.node(labels, {hash: nodeProperties.hash}, nodeProperties, {}) YIELD node AS n

        RETURN elementId(n) AS internal_id, nodeProperties.uid AS uid
        """
        # MERGE on `id` (not hash) for types in deduplicated_by_id: a globally-identified
        # Identifiable (e.g. ConceptDescription) re-emitted with differing content under the
        # same id collapses to one node (first-content-wins via empty onMatch), instead of
        # producing a second node that violates the id-uniqueness constraint.
        merge_by_id_query = """
        UNWIND keys($data) AS labelsString

        WITH split(labelsString, ",") AS labels, $data[labelsString] AS nodesProperties

        UNWIND nodesProperties AS nodeProperties
        CALL apoc.merge.node(labels, {id: nodeProperties.id}, nodeProperties, {}) YIELD node AS n

        RETURN elementId(n) AS internal_id, nodeProperties.uid AS uid
        """

        dedup_by_id = set(self.model_config.deduplicated_by_id)

        # Split into MERGE-by-id (dedup-by-id types), MERGE-by-hash (other dedup types, have a
        # `hash`) and plain CREATE.
        merge_data: Dict[str, List[Dict]] = {}
        merge_by_id_data: Dict[str, List[Dict]] = {}
        create_data: Dict[str, List[Dict]] = {}
        for label_tuple, node_list in grouped_nodes.items():
            key = ",".join(label_tuple)
            dedup = [n for n in node_list if "hash" in n]
            plain = [n for n in node_list if "hash" not in n]
            if dedup:
                if dedup_by_id.intersection(label_tuple):
                    merge_by_id_data[key] = dedup
                else:
                    merge_data[key] = dedup
            if plain:
                create_data[key] = plain

        uid_to_internal_id = {}
        if create_data:
            for record in session.run(create_nodes_query, data=create_data):
                uid_to_internal_id[record['uid']] = record['internal_id']
        if merge_data:
            for record in session.run(merge_nodes_query, data=merge_data):
                uid_to_internal_id[record['uid']] = record['internal_id']
        if merge_by_id_data:
            for record in session.run(merge_by_id_query, data=merge_by_id_data):
                uid_to_internal_id[record['uid']] = record['internal_id']

        return uid_to_internal_id

    def _create_relationships(self, session: Session, relationships: Dict[str, List],
                              uid_to_internal_id: Dict[int, int], db_batch_size: int = 10000):
        """Create relationships in Neo4j."""
        # MERGE (not CREATE) so a relationship onto a reused deduplicated node is not
        # duplicated when the same edge is seen again by a later import. The relationship
        # properties (e.g. list_index) are part of the merge key, so distinct list
        # positions remain distinct edges.
        create_rels_query = """
        UNWIND $relationships AS rel
        MATCH (from_node) WHERE elementId(from_node) = rel.from_id
        MATCH (to_node) WHERE elementId(to_node) = rel.to_id
        CALL apoc.merge.relationship(from_node, rel.rel_type, rel.rel_props, {}, to_node, {}) YIELD rel AS r
        RETURN count(*) AS created
        """

        all_rels = []
        for rel_type, rel_list in relationships.items():
            for rel in rel_list:
                try:
                    all_rels.append({
                        'from_id': uid_to_internal_id[rel['from_uid']],
                        'to_id': uid_to_internal_id[rel['to_uid']],
                        'rel_type': rel_type,
                        'rel_props': rel['rel_props']
                    })
                except KeyError:
                    logger.warning(
                        f"Skipping relationship {rel_type} from {rel['from_uid']} to {rel['to_uid']} due to missing UID mapping.")

        created_rels = 0
        for i in range(0, len(all_rels), db_batch_size):
            batch = all_rels[i:i + db_batch_size]
            # Do not swallow a TransientError here: it would drop the whole batch of edges
            # while reporting success, silently corrupting the graph. Let it propagate so the
            # caller's transaction/retry handling surfaces the failure (node creation behaves
            # the same way).
            result = session.run(create_rels_query, relationships=batch)
            created_rels += result.single()['created']

        return created_rels

    def _deduplicate_nodes(self, grouped_nodes: dict[tuple[str], list[dict]]):
        dedup_by_id = set(self.model_config.deduplicated_by_id)
        for label_tuple, nodes in grouped_nodes.items():
            # Check if any of the labels in this tuple should be deduplicated
            if not any(lbl in self.model_config.deduplicated_object_types for lbl in label_tuple):
                continue

            # Dedup-by-id types collapse on their Identifiable id (first-content-wins) so two
            # nodes that share an id but differ in content don't both survive to creation; the
            # rest dedup on content hash. The hash is still assigned (backs the hash index).
            by_id = bool(dedup_by_id.intersection(label_tuple))
            filtered_nodes = []
            for node in nodes:
                # Deterministic JSON hash from properties
                node_copy = {k: v for k, v in node.items() if k != "uid"}
                hash_value = hashlib.sha256(json.dumps(node_copy, sort_keys=True).encode()).hexdigest()
                dedup_key = node["id"] if by_id else hash_value

                if dedup_key in self.deduplicated_nodes:
                    # This node already exists (deduplicate)
                    existing_uid = self.deduplicated_nodes[dedup_key]
                    self.deduplicated_to_existing_uid_map[node["uid"]] = existing_uid
                else:
                    node["hash"] = hash_value
                    # First time we see this node -> keep it
                    self.deduplicated_nodes[dedup_key] = node["uid"]
                    filtered_nodes.append(node)

            # Replace node list with deduplicated version
            grouped_nodes[label_tuple] = filtered_nodes
        return grouped_nodes

    def _deduplicate_rels(self, relationships: dict[str, list[dict]]):
        # --- 🔧 Rewrite relationships to use deduplicated UIDs ---
        for rel_type, rel_list in relationships.items():
            updated_rels = []
            for rel in rel_list:
                # Update UIDs if they exist in the deduplicated map
                rel["from_uid"] = self.deduplicated_to_existing_uid_map.get(rel["from_uid"], rel["from_uid"])
                rel["to_uid"] = self.deduplicated_to_existing_uid_map.get(rel["to_uid"], rel["to_uid"])

                # Deterministic JSON hash from relationship type and properties
                hash_value = hashlib.sha256(
                    json.dumps({"_type": rel_type, **rel}, sort_keys=True).encode()
                ).hexdigest()

                if hash_value not in self.deduplicated_rels:
                    self.deduplicated_rels.add(hash_value)
                    updated_rels.append(rel)

            # Replace with deduplicated and updated relationships
            rel_list[:] = updated_rels

        return relationships

    def _upload_nodes_and_relationships(self, nodes: List[Dict], relationships: Dict[str, List],
                                        stats: UploadStats = None,
                                        exist_uid_to_internal_id: Optional[Dict[int, int]] = None,
                                        db_batch_size: int = 1000) -> UploadStats:
        """Upload nodes and relationships to Neo4j in a single transaction."""
        if stats is None:
            stats = UploadStats()

        # Reset the in-memory dedup/uid caches for this logical write. They must NOT outlive a
        # single upload: after a delete + re-add on the same client they would still map a
        # Reference hash to a now-deleted uid/elementId, so the Reference would not be
        # recreated and its edge would dangle. Cross-process dedup is guaranteed at the DB
        # level by apoc.merge.node on `hash`; this map is only a within-write optimization.
        self.deduplicated_nodes.clear()
        self.deduplicated_to_existing_uid_map.clear()
        self.deduplicated_rels.clear()
        self.uid_to_internal_id.clear()
        # Invalidate the cached containment relationship-type filter (AASNeo4JClient): this
        # upload may introduce a new relationship type (e.g. the first :submodels edge when a
        # shell is stored after its submodel), and a stale cached filter would omit it and break
        # incremental :references resolution. Recomputed lazily on the next subgraph fetch.
        self._containment_rel_filter_cache = None

        # Group nodes and filter relationships for this batch
        grouped_nodes = self._group_nodes_by_label(nodes)

        # --- 🔧 Deduplication steps ---
        # TODO: the deduplication data should be saved late in a DB
        # TODO: Consider the deduplication while CRUD operations on AAS Server
        grouped_nodes = self._deduplicate_nodes(grouped_nodes)
        relationships = self._deduplicate_rels(relationships)

        # --- Continue with database operations ---
        with self.driver.session() as session:
            # 1. Create Nodes in Batches
            node_start_time = time.time()
            created_map = self._create_nodes(session, grouped_nodes)
            self.uid_to_internal_id.update(created_map)
            if exist_uid_to_internal_id:
                # Merge existing UID to internal ID mapping with newly created nodes
                self.uid_to_internal_id.update(exist_uid_to_internal_id)

            node_creation_time = time.time() - node_start_time
            stats.total_node_creation_time += node_creation_time
            node_count = sum(len(nodes) for nodes in grouped_nodes.values())
            stats.total_nodes_created += node_count
            logger.info(f"Created {node_count} nodes in {node_creation_time:.2f} seconds")

            # 2. Create Relationships in Batches
            rel_start_time = time.time()
            relationship_count = self._create_relationships(session, relationships, self.uid_to_internal_id, db_batch_size)
            relationship_creation_time = time.time() - rel_start_time
            stats.total_relationship_creation_time += relationship_creation_time
            stats.total_relationships_created += relationship_count
            logger.info(f"Created {relationship_count} relationships in {relationship_creation_time:.2f} seconds")

            # 3. Cleanup UIDs in Batches — uid is an import-internal tracking property and
            # must not persist on nodes. Only this batch's nodes are touched (the in-memory
            # uid -> elementId map is kept for later relationship wiring). hash is preserved
            # for deduplication.
            self._cleanup_uids_in_session(session, list(created_map.values()), db_batch_size)

        del grouped_nodes

        return stats

    def _cleanup_uids_in_session(self, session: Session, internal_ids: List[int], batch_size: int):
        """Removes `uid` property from nodes in batches within an existing session."""
        delete_query = """
        UNWIND $ids AS id
        MATCH (n)
        WHERE elementId(n) = id
        REMOVE n.uid
        """
        for i in range(0, len(internal_ids), batch_size):
            batch_ids = internal_ids[i:i + batch_size]
            try:
                session.run(delete_query, ids=batch_ids)
            except ClientError as e:
                logging.warning(f"Error during UID cleanup for batch: {e}")

    @staticmethod
    def identify_labels(obj: Dict):
        return ("Unknown",)

    def _process_dict(self, obj: Dict, node_properties: Optional[Dict[str, any]] = None) \
            -> Tuple[List[Dict], Dict[str, List]]:
        nodes = []
        relationships: dict[str, dict[str, str]] = {}
        node_properties = node_properties or {}

        node_uid = self._gen_unique_node_name()
        node_labels = self.identify_labels(obj)
        node_properties.update({
            'uid': node_uid,
            'labels': node_labels
        })

        # unpack the DICTS_TO_PROPERTY_LISTS
        list_of_dicts_prop_as_multiple_list_props = self.get_props_to_model_as_multiple_lists(node_labels)
        dict_prop_as_multiple_props = self.get_complex_props_to_model_as_multiple_simple_props(node_labels)

        for key, value in obj.items():
            if key in self.model_config.keys_to_ignore:
                continue
            elif key in list_of_dicts_prop_as_multiple_list_props:
                # BEFORE: keys = [{"type": "GlobalReference", "value": "0173-1#02-AAW001#001"}}, ...]
                # AFTER:  keys_type = ["GlobalReference", ...]
                #         keys_value = ["0173-1#02-AAW001#001", ...]
                if value:
                    for dict_key in value[0].keys():
                        node_properties[f"{key}_{dict_key}"] = [dict_[dict_key] for dict_ in value]
            elif key in dict_prop_as_multiple_props:
                if value:
                    child_nodes, child_rels = self._process_dict(value)
                    if len(child_nodes) > 1:
                        raise ValueError(f"The dict should have only one child node, got {len(child_nodes)}: {value}")

                    for sub_key, sub_value in child_nodes[0].items():
                        if sub_key not in ("uid", "labels"):
                            node_properties[f"{key}_{sub_key}"] = sub_value

                    rels = {
                        f"{key}_{rel_type}": {**rel, "from_uid": node_uid}
                        for rel_type, rel in child_rels.items()
                    }
                    self._merge_relationships(relationships, rels)

            elif isinstance(value, dict):
                child_nodes, child_rels = self._process_dict(value)
                nodes.extend(child_nodes)
                self._merge_relationships(relationships, child_rels)

                # Create relationship to the last created node. Containment is represented
                # only by the semantic edge (named after the attribute); no separate :child
                # edge — navigation traverses the semantic edges directly.
                if child_nodes:
                    self._add_relationship(relationships, key, node_uid, child_nodes[-1]['uid'])

            elif isinstance(value, list):
                for i, item in enumerate(value, start=0):
                    if not isinstance(item, dict):
                        logger.warning(f"Unsupported type in list: {type(item)}")
                        continue

                    child_nodes, child_rels = self._process_dict(item)
                    nodes.extend(child_nodes)
                    self._merge_relationships(relationships, child_rels)

                    # Create relationship to direct child node, which is the last one
                    if child_nodes:
                        rel_props = {"is_list": True}

                        if self.model_config.all_list_item_relationships_have_index is True:
                            rel_props["list_index"] = i
                        elif self.model_config.list_item_relationships_with_index:
                            for node_label in node_labels:
                                if key in self.model_config.list_item_relationships_with_index.get(node_label, []):
                                    rel_props["list_index"] = i
                                    break
                        self._add_relationship(relationships, key, node_uid, child_nodes[-1]['uid'],
                                               rel_props=rel_props)

            else:
                node_properties[key] = value

        # Denormalize the first key of a Reference into an indexed scalar `target_id`
        # (== keys_value[0], the targeted Identifiable's id). References are content-
        # addressed/immutable, so this never needs re-syncing. Used for indexed lookup of
        # "references targeting id X" during incremental resolution.
        # `target_id_base` is the version-agnostic IRDI base of target_id, so ECLASS
        # semanticIds can be matched across versions by indexed equality (see irdi_base).
        if "Reference" in node_labels and node_properties.get("keys_value"):
            node_properties["target_id"] = node_properties["keys_value"][0]
            node_properties["target_id_base"] = irdi_base(node_properties["target_id"])

        # Version-agnostic IRDI base for ConceptDescription ids, so an ECLASS concept can
        # be looked up across all its versions by indexed equality on `id_base`.
        if "ConceptDescription" in node_labels and node_properties.get("id"):
            node_properties["id_base"] = irdi_base(node_properties["id"])

        nodes.append(node_properties)
        return nodes, relationships

    def _process_json_data(self, json_data: Dict[str, Any]) -> Tuple[List[Dict], Dict[str, List]]:
        """
        Process JSON data into nodes and relationships.

        This method can be overloaded in child classes to process specific dicts, like AAS Environment serializations,
        where high-level keys like 'assetAdministrationShells', 'submodels' or 'conceptDescriptions' should be skipped.
        """
        return self._process_dict(json_data)

    def _process_json_file(self, file_path: str) -> Tuple[List[Dict], Dict[str, List]]:
        """Process a single JSON file and return nodes and relationships."""
        with open(file_path, encoding='utf-8') as f:
            data = json.load(f)
            nodes, relationships = self._process_json_data(data)
        return nodes, relationships

    def _process_json_files_batch(self, directory: str, files_batch: List[str]) -> Tuple[List[Dict], Dict[str, List]]:
        """Process a batch of JSON files and return nodes and relationships."""
        batch_nodes = []
        batch_relationships = {}
        for filename in files_batch:
            nodes, relationships = self._process_json_file(join(directory, filename))
            batch_nodes.extend(nodes)
            self._merge_relationships(batch_relationships, relationships)
        return batch_nodes, batch_relationships

    def upload_all_json_from_dir(self, directory: str, file_batch_size: int = 50,
                                 db_batch_size: int = 10000, max_num_of_batches=10000) -> UploadStats:
        """Upload JSON files from directory into Neo4j using batch processing."""
        stats = UploadStats()
        json_files = [f for f in os.listdir(directory) if isfile(join(directory, f)) and f.endswith('.json')]
        stats.total_files = len(json_files)
        stats.total_batches = (stats.total_files + file_batch_size - 1) // file_batch_size

        logger.info(f"Found {stats.total_files} JSON files in directory '{directory}'")
        logger.info(f"File batch size: {stats.total_batches} batches of {file_batch_size} files each")
        logger.info(f"Database transaction batch size: {db_batch_size}")

        # Process files in batches
        for batch_num in range(stats.total_batches):
            start_idx = batch_num * file_batch_size
            end_idx = min(start_idx + file_batch_size, stats.total_files)
            current_file_batch = json_files[start_idx:end_idx]

            logger.info(f"\n--- Processing Batch {batch_num + 1}/{stats.total_batches} ---")
            logger.info(f"Files {start_idx + 1}-{end_idx} of {stats.total_files}")

            # Process current batch
            batch_start_time = time.time()
            batch_nodes, batch_relationships = self._process_json_files_batch(directory, current_file_batch)

            processing_time = time.time() - batch_start_time
            stats.total_processing_time += processing_time
            logger.info(f"Processed {len(current_file_batch)} files in {processing_time:.2f} seconds")

            if not batch_nodes:
                logger.info("No nodes to create in this batch, skipping...")
                continue

            self._upload_nodes_and_relationships(batch_nodes, batch_relationships, stats, db_batch_size=db_batch_size)

            batch_total_time = time.time() - batch_start_time
            logger.info(f"Batch {batch_num + 1} completed in {batch_total_time:.2f} seconds")

            if batch_num == max_num_of_batches:
                logger.warning("Max number of batches reached")
                break

        stats.finish()
        return stats

    def upload_json_file(self, file_path: str, db_batch_size: int = 1000):
        nodes, relationships = self._process_json_file(file_path)
        stats = self._upload_nodes_and_relationships(nodes, relationships, db_batch_size=db_batch_size)
        stats.finish()

    def upload_json(self, json_data: Dict[str, Any], db_batch_size: int = 1000):
        nodes, relationships = self._process_json_data(json_data)
        stats = self._upload_nodes_and_relationships(nodes, relationships, db_batch_size=db_batch_size)
        stats.finish()
