MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Pressure'})
WHERE sm0.idShort = 'TechnicalData' AND sme0.value < 200
RETURN sm0