MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Checksum'})
WHERE '16#' + apoc.text.format('%X', [toInteger(coalesce(sme0.value_text, [sme0.value]))]) = '16#FF00'
RETURN sm
