MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Checksum'})
WHERE toString(sme0.value) = '16#FF00'
RETURN sm
