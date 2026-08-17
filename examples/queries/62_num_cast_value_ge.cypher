MATCH (sm:Submodel)-[:submodelElements|value|statements|annotations*1..]->(sme0:SubmodelElement)
WHERE sme0.idShort = 'MaxAmbientTemperature' AND any(v0 IN [c0 IN coalesce(sme0.value_text, [sme0.value]) | toFloat(c0)] WHERE v0 >= 60)
RETURN DISTINCT sm