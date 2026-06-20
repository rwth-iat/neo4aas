MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'ManufacturerName'})
WHERE any(v0 IN sme0.value_language WHERE v0 = 'en')
RETURN sm
