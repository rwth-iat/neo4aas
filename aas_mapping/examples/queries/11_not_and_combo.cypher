MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Status'}),
      (sm0)-[:submodelElements]->(sme1:SubmodelElement {idShort: 'Priority'})
WHERE NOT (sme0.value = 'Inactive' AND sme1.value < 3)
RETURN sm0