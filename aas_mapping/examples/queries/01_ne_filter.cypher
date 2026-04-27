MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Material'})
WHERE sme0.value <> 'Plastic'
RETURN sm0