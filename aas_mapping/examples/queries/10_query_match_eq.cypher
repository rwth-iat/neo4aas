MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Documents'})-[:value]->(sme1:SubmodelElement)-[:value]->(sme2:SubmodelElement {idShort: 'DocumentClassification'})-[:value]->(sme3:SubmodelElement {idShort: 'Class'})
MATCH (sm:Submodel)-[:submodelElements]->(sme4:SubmodelElement {idShort: 'Documents'})-[:value]->(sme5:SubmodelElement)-[:value]->(sme6:SubmodelElement {idShort: 'DocumentVersion'})-[:value]->(sme7:SubmodelElement {idShort: 'SMLLanguages'})-[:value]->(sme8:SubmodelElement)
WHERE sme3.value = '03-01' AND sme8.value_language IN 'nl'
RETURN sm
