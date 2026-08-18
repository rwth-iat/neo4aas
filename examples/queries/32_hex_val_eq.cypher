MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Checksum'})
WHERE any(v0 IN coalesce(apoc.convert.toList(sme0.value_text), [sme0.value]) WHERE v0 = '16#1A2F')
RETURN DISTINCT sm
