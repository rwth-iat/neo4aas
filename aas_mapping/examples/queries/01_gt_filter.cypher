MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Temperature'})
WHERE sme0.value > 50
RETURN sm0