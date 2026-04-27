MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Versions'}),
      (sme0)-[:value {list_index: 0}]->(sme1:SubmodelElement),
      (sme1)-[:value]->(sme2:SubmodelElement {idShort: 'VersionId'})
WHERE sme2.value = '1.0'
RETURN sm0