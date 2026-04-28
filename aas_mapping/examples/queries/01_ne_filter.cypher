MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Material'})
WHERE sme0.value <> 'Plastic'
RETURN sm
