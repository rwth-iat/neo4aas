MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'ProductCode'})
WHERE any(v0 IN coalesce(sme0.value_text, [sme0.value]) WHERE v0 STARTS WITH 'ABC-')
RETURN DISTINCT sm
