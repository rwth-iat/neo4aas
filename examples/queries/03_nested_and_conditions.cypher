MATCH (sm:Submodel)
MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Pressure'})
WHERE sm.idShort = 'TechnicalData' AND any(v0 IN coalesce(apoc.convert.toList(sme0.value_text), [sme0.value]) WHERE v0 < 200)
RETURN DISTINCT sm
