MATCH (sm:Submodel)-[:submodelElements|value|statements|annotations*1..]->(sme0:SubmodelElement)-[:semanticId]->(semanticId0:Reference)
MATCH (sm:Submodel)-[:submodelElements|value|statements|annotations*1..]->(sme1:SubmodelElement)-[:semanticId]->(semanticId1:Reference)
WHERE semanticId0.target_id = '0173-1#02-AAY818#001' AND any(v0 IN [c0 IN coalesce(sme0.value_text, [sme0.value]) | toFloat(c0)] WHERE v0 <= -40) AND semanticId1.target_id = '0173-1#02-AAY819#001' AND any(v1 IN [c1 IN coalesce(sme1.value_text, [sme1.value]) | toFloat(c1)] WHERE v1 >= 120)
RETURN DISTINCT sm