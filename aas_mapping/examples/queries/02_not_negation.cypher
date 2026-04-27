MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Status'})
WHERE NOT (sme0.value = 'Inactive')
RETURN sm0