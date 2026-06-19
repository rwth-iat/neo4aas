MATCH (aas:AssetAdministrationShell)
MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'ProductClassifications'})-[:value]->(sme1:SubmodelElement {idShort: 'ProductClassificationItem'})-[:value]->(sme2:SubmodelElement {idShort: 'ProductClassId'})
MATCH (aas)-[:submodels]->(:Reference)-[:references]->(sm)
WHERE aas.idShort = 'MyShell' AND sme2.value = '27-37-09-05'
RETURN aas
