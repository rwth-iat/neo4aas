MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Color'})
MATCH (sm:Submodel)-[:submodelElements]->(sme1:SubmodelElement {idShort: 'Size'})
WHERE sme0.value = 'Blue' AND sme1.value > 50
RETURN sm
