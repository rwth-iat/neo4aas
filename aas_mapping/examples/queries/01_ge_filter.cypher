MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Weight'})
WHERE sme0.value >= 100
RETURN sm0