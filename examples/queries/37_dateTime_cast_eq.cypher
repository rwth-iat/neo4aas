MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Created'})
WHERE any(v0 IN [c0 IN coalesce(apoc.convert.toList(sme0.value_text), [sme0.value]) | datetime(c0)] WHERE v0 = datetime("2026-01-01T00:00:00Z"))
RETURN DISTINCT sm
