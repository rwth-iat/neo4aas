MATCH (aas:AssetAdministrationShell)
WHERE toString(aas.id) = 'myAAS'
RETURN DISTINCT aas
