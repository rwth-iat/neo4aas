MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'OpenTime'})
WHERE time(coalesce(sme0.value_text, [sme0.value])) = time("09:00:00")
RETURN sm
