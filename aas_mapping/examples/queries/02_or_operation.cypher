MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Material'}),
      (sm0)-[:submodelElements]->(sme1:SubmodelElement {idShort: 'Weight'})
WHERE sme0.value = 'Metal' OR sme1.value <= 50
RETURN sm0