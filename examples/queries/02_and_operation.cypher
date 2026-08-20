MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Color'})
MATCH (sm:Submodel)-[:submodelElements]->(sme1:SubmodelElement {idShort: 'Size'})
WHERE any(v0 IN coalesce(apoc.convert.toList(sme0.value_text), [sme0.value]) WHERE v0 = 'Blue') AND any(v1 IN coalesce(apoc.convert.toList(sme1.value_text), [sme1.value]) WHERE v1 > 50)
RETURN DISTINCT sm
