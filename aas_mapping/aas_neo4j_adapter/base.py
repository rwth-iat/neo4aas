import logging
from dataclasses import dataclass
from typing import List, Iterable, Dict, Optional

import neo4j
from neo4j import Driver

logger = logging.getLogger(__name__)

CypherClause = str

@dataclass
class Neo4jModelConfig:
    default_optimization_clauses: Iterable[str]
    deduplicated_object_types: Iterable[str]
    list_of_dicts_prop_as_multiple_list_props: Dict[str, List[str]]
    dict_prop_as_multiple_props: Dict[str, List[str]]
    virtual_relationships: Iterable[str]
    keys_to_ignore: Iterable[str]

    # If True, all relationships which represent a belonging of a Node to a list will have an index according to list item index
    # If False, all relationships will not have an Index
    # If Dict, only relationships belonging to attributes given in the List[str] of config Dict which are attributes
    # of nodes with label given in Dict key will have an item index
    all_list_item_relationships_have_index: bool
    list_item_relationships_with_index: Dict[str, List[str]]

EMPTY_NEO4J_MODEL_CONFIG = Neo4jModelConfig(
    default_optimization_clauses=[],
    deduplicated_object_types=[],
    list_of_dicts_prop_as_multiple_list_props={},
    dict_prop_as_multiple_props={},
    virtual_relationships=[],
    keys_to_ignore=[],
    all_list_item_relationships_have_index=True,
    list_item_relationships_with_index={},
)


class BaseNeo4JClient:
    driver: Driver
    model_config: Neo4jModelConfig

    def __init__(self, uri: str, user: str , password: Optional[str] = None, model_config: Neo4jModelConfig = None,
                 fix_on_import: bool = False):
        self.driver = neo4j.GraphDatabase.driver(uri, auth=(user, password)) if uri else None
        self.model_config = model_config or EMPTY_NEO4J_MODEL_CONFIG
        # Opt-in: when True, registered fixers (fixers.py) repair imported AAS data
        # (e.g. BCP 47 language tags) before it is stored. Off by default.
        self.fix_on_import = fix_on_import
        # Ensure schema (indexes/constraints) exists for every client. Idempotent: the
        # clauses use IF NOT EXISTS / are guarded in optimize_database, so this is a cheap
        # no-op once the schema is present.
        if self.driver is not None:
            self.optimize_database()

    def execute_clause(self, clause: CypherClause, single: bool = False, params: dict | None = None):
        """Execute the generated Cypher clauses in the Neo4j database. After execution, the clauses are cleared."""
        with self.driver.session() as session:
            if single:
                result = session.run(clause, **(params or {})).single()
            else:
                result = session.run(clause, **(params or {}))
                if result:
                    result = [record for record in result]
            return result

    def get_props_to_model_as_multiple_lists(self, node_labels: Iterable[str]) -> List[str]:
        """Return list-of-dicts properties to model as multiple lists."""
        return [
            prop
            for label in node_labels
            if label in self.model_config.list_of_dicts_prop_as_multiple_list_props
            for prop in self.model_config.list_of_dicts_prop_as_multiple_list_props[label]
        ]

    def get_complex_props_to_model_as_multiple_simple_props(self, node_labels: Iterable[str]) -> List[str]:
        """Return dict properties to model as multiple simple properties."""
        return [
            prop
            for label in node_labels
            if label in self.model_config.dict_prop_as_multiple_props
            for prop in self.model_config.dict_prop_as_multiple_props[label]
        ]

    def optimize_database(self):
        """Optimize the Neo4j database by creating all necessary indexes.

        Beyond the static clauses, a `hash` index is created for every deduplicated type
        so the backing index always matches `deduplicated_object_types` (nodes of those
        types are MERGEd on `hash` during import).
        """
        clauses = list(self.model_config.default_optimization_clauses)
        clauses += [
            f"CREATE INDEX IF NOT EXISTS FOR (n:{label}) ON (n.hash)"
            for label in self.model_config.deduplicated_object_types
        ]
        for clause in clauses:
            try:
                self.execute_clause(clause, single=True)
            except neo4j.exceptions.ClientError as e:
                if e.code == "Neo.ClientError.Schema.EquivalentSchemaRuleAlreadyExists":
                    logger.info(f"Index already exists: {clause}")
                else:
                    logger.warning(f"Failed to create index: {clause}, Error: {e}")


    def _remove_all(self, batch_size = 10000):
        """
        Remove all nodes and relationships from the Neo4j database in batches.
        It prevents memory errors on large databases.
        """
        with self.driver.session() as session:
            while True:
                result = session.run(f"""
                    MATCH (n)
                    WITH n LIMIT {batch_size}
                    DETACH DELETE n
                    RETURN count(n) AS nodes_deleted
                """)
                nodes_deleted = result.single()["nodes_deleted"]
                logger.info(f"Deleted {nodes_deleted} nodes.")

                # If the number of nodes deleted is less than the batch size,
                # it means we have reached the end of the database.
                if nodes_deleted < batch_size:
                    break

    def _truncate_db(self, db_name="neo4j"):
        """
        Remove all nodes and relationships from the Neo4j database in batches.
        It prevents memory errors on large databases.
        """
        with self.driver.session(database="system") as session:  # must run on system DB
            session.run(f"CREATE OR REPLACE DATABASE {db_name};")
            logger.info(f"Database '{db_name}' truncated successfully.")
        session.close()

    def _remove_all_indexes_and_constraints(self):
        def drop_all_indexes_and_constraints(tx):
            # Drop all indexes
            indexes = tx.run("SHOW INDEXES YIELD name").values()
            for (name,) in indexes:
                tx.run(f"DROP INDEX `{name}`")
                print(f"Dropped index: {name}")

            # Drop all constraints
            constraints = tx.run("SHOW CONSTRAINTS YIELD name").values()
            for (name,) in constraints:
                tx.run(f"DROP CONSTRAINT `{name}`")
                print(f"Dropped constraint: {name}")

        with self.driver.session() as session:
            session.execute_write(drop_all_indexes_and_constraints)

    def save_clauses_to_file(self, file_name: str, clauses: CypherClause):
        """Save the generated Cypher clauses to a file."""
        with open(file_name, 'w', encoding='utf8') as file:
            file.write(clauses)
