MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'A'})
WHERE sme0.value = 'x'
RETURN sm
