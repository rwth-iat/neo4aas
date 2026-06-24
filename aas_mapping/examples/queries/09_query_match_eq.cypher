MATCH (sm:Submodel)-[:submodelElements]->(sme0:SubmodelElement {idShort: 'FileVersion'})-[:value]->(sme1:SubmodelElement)-[:value]->(sme2:SubmodelElement {idShort: 'FileVersionId'})
MATCH (sm:Submodel)-[:submodelElements]->(sme3:SubmodelElement {idShort: 'FileVersion'})-[:value]->(sme4:SubmodelElement)-[:value]->(sme5:SubmodelElement {idShort: 'FileName'})
WHERE any(v0 IN coalesce(sme2.value_text, [sme2.value]) WHERE v0 = '1.1') AND any(v1 IN coalesce(sme5.value_text, [sme5.value]) WHERE v1 = 'SomeFile')
RETURN DISTINCT sm
