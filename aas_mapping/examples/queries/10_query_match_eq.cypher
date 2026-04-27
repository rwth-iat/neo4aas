MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Documents'}),
      (sme0)-[:value]->(sme1:SubmodelElement),
      (sme1)-[:value]->(sme2:SubmodelElement {idShort: 'DocumentClassification'}),
      (sme2)-[:value]->(sme3:SubmodelElement {idShort: 'Class'}),
      (sme1)-[:value]->(sme4:SubmodelElement {idShort: 'DocumentVersion'}),
      (sme4)-[:value]->(sme5:MultiLanguageProperty {idShort: 'SMLLanguages'})
WHERE sme3.value = '03-01' AND 'nl' IN sme5.value_language
RETURN sm0