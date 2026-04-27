MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'IsActive'})
WHERE sme0.value = true
RETURN sm0