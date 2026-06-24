MATCH (aas:AssetAdministrationShell)
WHERE aas.idShort = 'MyShell'
RETURN DISTINCT aas.id
