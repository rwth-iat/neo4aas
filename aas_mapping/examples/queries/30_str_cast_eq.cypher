MATCH (aas:AssetAdministrationShell)
WHERE toString(aas.id) = 'myAAS'
RETURN aas
