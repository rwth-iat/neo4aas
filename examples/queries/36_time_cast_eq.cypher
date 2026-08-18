MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'OpenTime'})
WHERE any(v0 IN [c0 IN coalesce(apoc.convert.toList(sme0.value_text), [sme0.value]) | time(c0)] WHERE v0 = time("09:00:00"))
RETURN DISTINCT sm
