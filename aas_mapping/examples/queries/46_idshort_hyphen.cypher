MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'my-element'})
WHERE sme0.value = 'test'
RETURN sm
