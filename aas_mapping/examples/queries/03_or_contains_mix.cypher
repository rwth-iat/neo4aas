MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Description'})
WHERE sme0.value CONTAINS 'urgent' OR sm0.idShort = 'MaintenanceLog'
RETURN sm0