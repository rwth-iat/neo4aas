MATCH (aas:AssetAdministrationShell),
      (aas)-[:assetInformation]->(ai0:AssetInformation),
      (ai0)-[:specificAssetIds]->(sa0),
      (aas)-[:assetInformation]->(ai1:AssetInformation),
      (ai1)-[:specificAssetIds]->(sa1)
WHERE sa0.name = 'supplierId' AND sa1.value = 'supplier-007'
RETURN aas