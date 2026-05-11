MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'arr'})-[:value {list_index: 1}]->(sme1:SubmodelElement)-[:value {list_index: 2}]->(sme2:SubmodelElement)
WHERE sme2.value = 'foo'
RETURN sm
