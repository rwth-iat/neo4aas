MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'SerialNumber'})
WHERE sme0.value =~ 'SN[0-9]{4}'
RETURN sm
