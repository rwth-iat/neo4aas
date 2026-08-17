MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'my-element'})
WHERE any(v0 IN coalesce(sme0.value_text, [sme0.value]) WHERE v0 = 'test')
RETURN DISTINCT sm
