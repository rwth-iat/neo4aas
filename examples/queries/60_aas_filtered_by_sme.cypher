MATCH (aas:AssetAdministrationShell)
MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'ProductClassifications'})-[:value]->(sme1:SubmodelElement {idShort: 'ProductClassificationItem'})-[:value]->(sme2:SubmodelElement {idShort: 'ProductClassId'})
MATCH (aas)-[:submodels]->(:Reference)-[:references]->(sm)
WHERE aas.idShort = 'MyShell' AND any(v0 IN coalesce(sme2.value_text, [sme2.value]) WHERE v0 = '27-37-09-05')
RETURN DISTINCT aas
