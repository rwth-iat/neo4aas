MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Code'})
WHERE toString(sme0.value) = '42'
RETURN sm0