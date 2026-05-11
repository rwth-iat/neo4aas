MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Weight'})
WHERE sme0.value >= 100
RETURN sm
