MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Checksum'})
WHERE any(v0 IN [c0 IN coalesce(sme0.value_text, [sme0.value]) | '16#' + apoc.text.format('%X', [toInteger(c0)])] WHERE v0 = '16#FF00')
RETURN DISTINCT sm