MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'OpeningHours'})
WHERE any(v0 IN coalesce(apoc.convert.toList(sme0.value_text), [sme0.value]) WHERE v0 = time("14:30:00"))
RETURN DISTINCT sm
