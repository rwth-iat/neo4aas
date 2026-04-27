MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Material'}),
      (sm0)-[:submodelElements]->(sme1:SubmodelElement {idShort: 'Weight'})
WHERE (sme0.value = 'Steel' OR sme0.value = 'Aluminum') AND sme1.value < 200
RETURN sm0