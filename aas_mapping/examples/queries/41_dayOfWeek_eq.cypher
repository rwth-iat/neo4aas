MATCH (sm:Submodel)
WHERE sm.idShort = 'EventLog' AND datetime("2026-04-28T00:00:00Z").dayOfWeek = 2
RETURN sm
