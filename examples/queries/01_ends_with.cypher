MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'ProductCode'})
WHERE any(v0 IN coalesce(apoc.convert.toList(sme0.value_text), [sme0.value]) WHERE v0 ENDS WITH '-XYZ')
RETURN DISTINCT sm
