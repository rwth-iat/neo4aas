MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Description'})
MATCH (sm:Submodel)
WHERE (any(v0 IN coalesce(apoc.convert.toList(sme0.value_text), [sme0.value]) WHERE v0 CONTAINS 'urgent') OR sm.idShort = 'MaintenanceLog')
RETURN DISTINCT sm
