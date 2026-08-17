from neo4aas.core.client import AASNeo4JClient, AAS_NEO4J_MODEL_CONFIG
from neo4aas.core.validation import AASConstraintChecker

client = AASNeo4JClient("bolt://localhost:7687", "neo4j", "12345678", AAS_NEO4J_MODEL_CONFIG)
report = AASConstraintChecker(client).check_all()
print(report.summary())