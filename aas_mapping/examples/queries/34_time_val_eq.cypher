MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'OpeningHours'})
WHERE sme0.value = time("14:30:00")
RETURN sm
