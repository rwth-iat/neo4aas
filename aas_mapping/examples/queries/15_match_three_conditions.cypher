MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'OrderItems'}),
      (sme0)-[:value]->(sme1:SubmodelElement),
      (sme1)-[:value]->(sme2:SubmodelElement {idShort: 'ProductId'}),
      (sme1)-[:value]->(sme3:SubmodelElement {idShort: 'Quantity'}),
      (sme1)-[:value]->(sme4:SubmodelElement {idShort: 'Status'})
WHERE sme2.value = 'P-1234' AND sme3.value > 0 AND sme4.value = 'Shipped'
RETURN sm0