MATCH (aas:AssetAdministrationShell),
      (aas)-[:assetInformation]->(ai0:AssetInformation)
WHERE ai0.globalAssetId STARTS WITH 'urn:example:'
RETURN aas