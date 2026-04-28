MATCH (sm:Submodel)
MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Pressure'})
WHERE sm.idShort = 'TechnicalData' AND sme0.value < 200
RETURN sm
