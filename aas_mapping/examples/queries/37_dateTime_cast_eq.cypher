MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Created'})
WHERE datetime(sme0.value) = datetime("2026-01-01T00:00:00Z")
RETURN sm
