MATCH (aas:AssetAdministrationShell)-[:submodels {list_index: 0}]->(submodels0:Reference)
WHERE submodels0.keys_value[0] = 'urn:sm/1'
RETURN aas
