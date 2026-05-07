MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Material'})
MATCH (sm:Submodel)-[:submodelElements]->(sme1:SubmodelElement {idShort: 'Weight'})
WHERE (sme0.value = 'Steel' OR sme0.value = 'Aluminum') AND sme1.value < 200
RETURN sm
