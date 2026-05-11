MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'FileVersion'})-[:value]->(sme1:SubmodelElement)-[:value]->(sme2:SubmodelElement {idShort: 'FileVersionId'})
MATCH (sm:Submodel)-[:submodelElements]->(sme3:SubmodelElement {idShort: 'FileVersion'})-[:value]->(sme4:SubmodelElement)-[:value]->(sme5:SubmodelElement {idShort: 'FileName'})
WHERE sme2.value = '1.1' AND sme5.value = 'SomeFile'
RETURN sm
