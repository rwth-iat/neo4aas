MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Timestamp'})
WHERE sme0.value = datetime("2026-04-28T12:00:00Z")
RETURN sm
