MATCH (sm:Submodel)
MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'ProductClassifications'})-[:value]->(sme1:SubmodelElement {idShort: 'ProductClassificationItem'})-[:value]->(sme2:SubmodelElement {idShort: 'ProductClassId'})
MATCH (sm:Submodel)-[:submodelElements]->(sme3: SubmodelElement)-[:semanticId]->(semanticId0)
WHERE sm.idShort = 'TechnicalData' AND sme2.value = '27-37-09-05' AND sm.idShort = 'TechnicalData' AND semanticId0.keys_value[0] = '0173-1#02-BAF016#006' AND sme3.value < 100
RETURN sm
