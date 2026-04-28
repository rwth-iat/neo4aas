MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'OpenTime'})
WHERE time(sme0.value) = time("09:00:00")
RETURN sm
