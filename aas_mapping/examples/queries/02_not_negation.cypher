MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Status'})
WHERE NOT (sme0.value = 'Inactive')
RETURN sm
