MATCH (sm0:Submodel),
      (sm0)-[:semanticId]->(sid0)
WHERE sid0.keys_value[0] = 'https://admin-shell.io/idta/TechnicalData/1/2' AND sm0.idShort = 'TechnicalData'
RETURN sm0