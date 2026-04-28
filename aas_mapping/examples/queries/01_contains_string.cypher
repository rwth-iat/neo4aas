MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Description'})
WHERE sme0.value CONTAINS 'high-quality'
RETURN sm
