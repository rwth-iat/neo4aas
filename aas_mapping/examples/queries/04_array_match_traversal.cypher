MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Components'}),
      (sme0)-[:value]->(sme1:SubmodelElement),
      (sme1)-[:value]->(sme2:SubmodelElement {idShort: 'Name'}),
      (sme1)-[:value]->(sme3:SubmodelElement {idShort: 'Status'})
WHERE sme2.value = 'Motor' AND sme3.value = 'Active'
RETURN sm0