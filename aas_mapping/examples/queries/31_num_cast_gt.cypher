MATCH (sm:Submodel)
WHERE toInteger(sm.id) > 100
RETURN sm
