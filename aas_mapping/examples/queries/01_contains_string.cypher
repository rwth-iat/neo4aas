MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Description'})
WHERE sme0.value CONTAINS 'high-quality'
RETURN sm0