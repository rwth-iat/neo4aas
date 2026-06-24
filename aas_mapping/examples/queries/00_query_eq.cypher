MATCH (aas:AssetAdministrationShell)
MATCH (aas:AssetAdministrationShell)-[:assetInformation]->(assetInformation0:AssetInformation)
WHERE aas.idShort = assetInformation0.assetType
RETURN DISTINCT aas
