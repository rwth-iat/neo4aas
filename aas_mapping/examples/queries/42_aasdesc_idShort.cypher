MATCH (aasdesc:AssetAdministrationShellDescriptor)
WHERE aasdesc.idShort = 'MyAASDescriptor'
RETURN DISTINCT aasdesc
