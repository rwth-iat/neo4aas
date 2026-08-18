MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Temperature'})
WHERE any(v0 IN coalesce(apoc.convert.toList(sme0.value_text), [sme0.value]) WHERE v0 > 50)
RETURN DISTINCT sm
