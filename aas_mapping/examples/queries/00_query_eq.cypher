MATCH (aas:AssetAdministrationShell),
      (aas)-[:assetInformation]->(ai0:AssetInformation)
WHERE aas.idShort = ai0.assetType
RETURN aas