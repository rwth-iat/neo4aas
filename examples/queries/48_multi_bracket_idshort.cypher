MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'arr'})-[:value {list_index: 1}]->(sme1:SubmodelElement)-[:value {list_index: 2}]->(sme2:SubmodelElement)
WHERE any(v0 IN coalesce(apoc.convert.toList(sme2.value_text), [sme2.value]) WHERE v0 = 'foo')
RETURN DISTINCT sm
