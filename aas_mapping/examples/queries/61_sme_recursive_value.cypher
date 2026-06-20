MATCH (sm:Submodel)-[:submodelElements|value|statements|annotations*1..]->(sme0:SubmodelElement)
WHERE any(v0 IN coalesce(sme0.value_text, [sme0.value]) WHERE v0 = 'red')
RETURN sm
