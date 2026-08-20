MATCH (sm:Submodel)
WHERE sm.idShort = 'EventLog' AND datetime("2026-04-28T00:00:00Z").year = 2026
RETURN DISTINCT sm
