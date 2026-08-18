MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'SerialNumber'})
WHERE any(v0 IN coalesce(apoc.convert.toList(sme0.value_text), [sme0.value]) WHERE v0 =~ 'SN[0-9]{4}')
RETURN DISTINCT sm
