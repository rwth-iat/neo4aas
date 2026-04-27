MATCH (sm0:Submodel),
      (sm0)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'SerialNumber'})
WHERE sme0.value ENDS WITH '-REV2'
RETURN sm0