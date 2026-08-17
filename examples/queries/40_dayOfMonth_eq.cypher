MATCH (sm:Submodel)
WHERE sm.idShort = 'EventLog' AND datetime("2026-04-28T00:00:00Z").day = 28
RETURN DISTINCT sm
