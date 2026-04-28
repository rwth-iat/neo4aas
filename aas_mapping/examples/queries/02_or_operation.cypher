MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Material'})
MATCH (sm:Submodel)-[:submodelElements]->-[:value]->(sme1:SubmodelElement {idShort: 'Weight'})
WHERE sme0.value = 'Metal' OR sme1.value <= 50
RETURN sm
