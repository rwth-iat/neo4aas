MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'ProductClassifications'}),
      (sme0)-[:value]->(sme1:SubmodelElement {idShort: 'ProductClassificationItem'}),
      (sme1)-[:value]->(sme2:SubmodelElement {idShort: 'ProductClassId'}),
      (sm0)-[:submodelElements]->(sme3:SubmodelElement),
      (sme3)-[:semanticId]->(sid0),
      (sm0)-[:submodelElements]->(sme4:SubmodelElement)
WHERE sm0.idShort = 'TechnicalData' AND sme2.value = '27-37-09-05' AND sm0.idShort = 'TechnicalData' AND sid0.keys_value[0] = '0173-1#02-BAF016#006' AND sme4.value < 100
RETURN sm0