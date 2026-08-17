from aas_mapping.aas_neo4j_adapter.aas_neo4j_client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG
from aas_mapping.aas_neo4j_adapter.validation import AASConstraintChecker

client = AASNeo4JClient("bolt://localhost:7687", "neo4j", "12345678", AAS_NEO4J_MODEL_CONFIG)
report = AASConstraintChecker(client).check_all()
print(report.summary())