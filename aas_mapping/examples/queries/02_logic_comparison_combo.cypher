MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Material'})
MATCH (sm:Submodel)-[:submodelElements]->(sme1:SubmodelElement {idShort: 'Material'})
MATCH (sm:Submodel)-[:submodelElements]->(sme2:SubmodelElement {idShort: 'Weight'})
WHERE sme0.value = 'Steel' OR sme1.value = 'Aluminum' AND sme2.value < 200
RETURN sm
