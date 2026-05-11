MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'ProductCode'})
WHERE sme0.value STARTS WITH 'ABC-'
RETURN sm
