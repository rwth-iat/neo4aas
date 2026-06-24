MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Components'})-[:value]->(sme1:SubmodelElement)-[:value]->(sme2:SubmodelElement {idShort: 'Name'})
MATCH (sm:Submodel)-[:submodelElements]->(sme3:SubmodelElement {idShort: 'Components'})-[:value]->(sme4:SubmodelElement)-[:value]->(sme5:SubmodelElement {idShort: 'Status'})
WHERE any(v0 IN coalesce(sme2.value_text, [sme2.value]) WHERE v0 = 'Motor') AND any(v1 IN coalesce(sme5.value_text, [sme5.value]) WHERE v1 = 'Active')
RETURN DISTINCT sm
