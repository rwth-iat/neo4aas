MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'ManufacturerName'})
WHERE 'en' IN sme0.value_language
RETURN sm
