MATCH (sm:Submodel)
WHERE toFloat(sm.id) > 100
RETURN DISTINCT sm
