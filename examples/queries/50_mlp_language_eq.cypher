MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'ManufacturerName'})
WHERE any(v0 IN apoc.convert.toList(sme0.value_language) WHERE v0 = 'en')
RETURN DISTINCT sm
