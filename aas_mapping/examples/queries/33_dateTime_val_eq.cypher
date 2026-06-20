MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Timestamp'})
WHERE any(v0 IN coalesce(sme0.value_text, [sme0.value]) WHERE v0 = datetime("2026-04-28T12:00:00Z"))
RETURN sm
