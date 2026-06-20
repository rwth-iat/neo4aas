MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Weight'})
WHERE any(v0 IN coalesce(sme0.value_text, [sme0.value]) WHERE v0 >= 100)
RETURN sm
