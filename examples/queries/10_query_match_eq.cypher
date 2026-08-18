MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'Documents'})-[:value]->(sme1:SubmodelElement)-[:value]->(sme2:SubmodelElement {idShort: 'DocumentClassification'})-[:value]->(sme3:SubmodelElement {idShort: 'Class'})
MATCH (sm:Submodel)-[:submodelElements]->(sme4:SubmodelElement {idShort: 'Documents'})-[:value]->(sme5:SubmodelElement)-[:value]->(sme6:SubmodelElement {idShort: 'DocumentVersion'})-[:value]->(sme7:SubmodelElement {idShort: 'SMLLanguages'})-[:value]->(sme8:SubmodelElement)
WHERE any(v0 IN coalesce(apoc.convert.toList(sme3.value_text), [sme3.value]) WHERE v0 = '03-01') AND any(v1 IN apoc.convert.toList(sme8.value_language) WHERE v1 = 'nl')
RETURN DISTINCT sm
