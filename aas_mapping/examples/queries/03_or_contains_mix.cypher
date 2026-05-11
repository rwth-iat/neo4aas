MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Description'})
MATCH (sm:Submodel)
WHERE (sme0.value CONTAINS 'urgent' OR sm.idShort = 'MaintenanceLog')
RETURN sm
