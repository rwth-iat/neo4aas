MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Components'})-[:value]->(sme1:SubmodelElement)-[:value]->(sme2:SubmodelElement {idShort: 'Name'})
MATCH (sm:Submodel)-[:submodelElements]->-[:value]->(sme3:SubmodelElement {idShort: 'Components'})-[:value]->(sme4:SubmodelElement)-[:value]->(sme5:SubmodelElement {idShort: 'Status'})
WHERE sme2.value = 'Motor' AND sme5.value = 'Active'
RETURN sm
