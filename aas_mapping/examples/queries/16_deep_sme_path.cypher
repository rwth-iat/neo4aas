MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Catalog'}),
      (sme0)-[:value]->(sme1:SubmodelElement {idShort: 'Manufacturer'}),
      (sme0)-[:value]->(sme2:SubmodelElement {idShort: 'ProductName'})
WHERE sme1.value = 'ACME Corp' AND sme2.value CONTAINS 'sensor'
RETURN sm0