import logging
from typing import Dict, List
from aas_mapping.aas_neo4j_adapter.base import BaseNeo4JClient

logger = logging.getLogger(__name__)


class JsonFromNeo4jExporter(BaseNeo4JClient):
    def _get_node_properties(self, node: Dict) -> Dict:
        return {key: value for key, value in node['properties'].items()}

    def _create_list_of_dicts(self, *lists: List[List[any]], keys: List[str]) -> List[Dict]:
        if len(keys) != len(lists):
            raise ValueError("Number of keys must match number of input lists.")

        objs = [dict(zip(keys, values)) for values in zip(*lists)]
        return objs

    def _sort_relationships_based_on_type_and_list_entries_by_index(self, relationships):
        return sorted(
            relationships,
            key=lambda x: (
                x.get("type"),  # Primary sort by type
                x.get("properties", {}).get("value", {}).get("list_index", float("inf"))  # Secondary sort
            )
        )

    def _merge_prefixed_props_back_to_list_of_dicts_prop(self, node_labels: list[str], node_properties: dict):
        props_with_list_of_dicts = self.get_props_to_model_as_multiple_lists(node_labels)
        if not props_with_list_of_dicts:
            return node_properties

        for original_prop in props_with_list_of_dicts:
            original_prop_prefix = original_prop + "_"

            # Find all keys that belong to this original_prop
            part_attr_keys = [key for key in node_properties if key.startswith(original_prop_prefix)]

            if not part_attr_keys:
                continue

            # Extract lists and strip the prefix from keys
            lists = [node_properties.pop(key) for key in part_attr_keys]
            keys = [key.removeprefix(original_prop_prefix) for key in part_attr_keys]
            node_properties[original_prop] = self._create_list_of_dicts(*lists, keys=keys)
        return node_properties

    def _merge_prefixed_props_back_to_dict_prop(self, node_labels: list[str], node_properties: dict):
        props_with_dicts = self.get_complex_props_to_model_as_multiple_simple_props(node_labels)
        if not props_with_dicts:
            return node_properties

        for original_prop in props_with_dicts:
            original_prop_prefix = original_prop + "_"

            # Find all keys that belong to this original_prop
            part_attr_keys = [key for key in node_properties if key.startswith(original_prop_prefix)]

            if not part_attr_keys:
                continue

            original_prop_value = {key.removeprefix(original_prop_prefix): node_properties.pop(key) for key in
                                   part_attr_keys}
            node_properties[original_prop] = original_prop_value
        return node_properties

    def _merge_relationships_in_node_data_dict(self, node: Dict, node_data_dict: Dict, relationships: List[Dict], subgraph: Dict):
        # Check if all given rels are starting in the given node
        for rel in relationships:
            if rel['start']['id'] != node['id']:
                raise ValueError("A relationship is not starting in the given node:", rel, node)

        # Remove virtual relationships
        relationships = [rel for rel in relationships if rel['label'] not in self.model_config.virtual_relationships]

        sorted_relationships = self._sort_relationships_based_on_type_and_list_entries_by_index(relationships)

        for rel in sorted_relationships:
            rel_type = rel['label']
            related_node = next(n for n in subgraph['nodes'] if n['id'] == rel['end']['id'])
            related_node_properties = self._get_node_properties(related_node)
            if "properties" in rel and "is_list" in rel['properties'] and rel['properties']["is_list"]:
                node_data_dict.setdefault(rel_type, []).append(related_node_properties)
                if "list_index" in rel['properties']:
                    list_index = rel['properties']["list_index"]
                    if len(node_data_dict[rel_type]) - 1 != list_index:
                        logger.warning(f"Index of the list does not match with the saved index in the graph:"
                                       f"{len(node_data_dict[rel_type]) - 1} != {list_index}")
                        logger.warning(str(sorted_relationships))
            else:
                node_data_dict[rel_type] = related_node_properties

            # Recursively process the related node if it has outgoing relationships
            outgoing_relationships = [r for r in subgraph['relationships'] if r['start']['id'] == related_node['id']]
            self._merge_relationships_in_node_data_dict(related_node, related_node_properties, outgoing_relationships,
                                                        subgraph)

            # FIXME: Take a look here, may be the lines below and above should be replaced
            # Handle specific keys for certain node types
            related_node_properties = self._merge_prefixed_props_back_to_list_of_dicts_prop(related_node['labels'],
                                                                                            related_node_properties)
            related_node_properties = self._merge_prefixed_props_back_to_dict_prop(related_node['labels'],
                                                                                   related_node_properties)

    def convert_subgraph_to_data_dict(self, subgraph: Dict) -> Dict:
        """Take a Neo4J subgraph and convert it to a data dictionary."""
        root_node = subgraph['nodes'][0]
        root_node_data_dict = self._get_node_properties(root_node)
        root_node_data_dict = self._merge_prefixed_props_back_to_list_of_dicts_prop(root_node['labels'], root_node_data_dict)
        root_node_data_dict = self._merge_prefixed_props_back_to_dict_prop(root_node['labels'], root_node_data_dict)
        root_node_outgoing_relationships = [r for r in subgraph['relationships'] if r['start']['id'] == root_node['id']]
        self._merge_relationships_in_node_data_dict(root_node, root_node_data_dict, root_node_outgoing_relationships, subgraph)
        return root_node_data_dict


