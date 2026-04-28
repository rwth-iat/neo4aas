MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Temperature'})
WHERE sme0.value > 50
RETURN sm
