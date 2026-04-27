MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'MaxTemperature'})
WHERE sme0.valueType = 'xs:double' AND sme0.value > 85
RETURN sm0