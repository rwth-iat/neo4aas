MATCH (aas:AssetAdministrationShell)
WHERE aas.idShort = 'MyShell'
RETURN aas.id
