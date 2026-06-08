MATCH (sm:Submodel)
WHERE toFloat(sm.id) > 100
RETURN sm
