# AAS Query Language

The AAS Query Language is a query language designed to query Asset Administration Shell (AAS) data structures. 
The full specification can be found in the [AAS Query Language Specification](https://industrialdigitaltwin.io/aas-specifications/IDTA-01002/v3.1/query-language.html).

Here are some notes on the AAS Query Language:
- $match - The $match operator signals that the following clauses (a) contain at least 1 list of elements with [] syntax, and that (b) all conditions shall be evaluated on the same element of this list. (https://admin-shell-io.github.io/aas-specs-antora/IDTA-01002/v3.1/query-language.html#_match_of_elements_in_lists)
- If $aas and $sm are used together, only the submodels referenced by the matching $aas are searched by the $sm expression. The same search principle is used, when combining $sm and $sme. In such case only the SubmodelElements which are part of matching submodels by $sm expression are searched by the $sme expression. Several such hierarchical conditions may even be combined by $match expressions.
- References include a list of keys, i.e. .keys[]. Very often the value of the first key in the list is needed, e.g. for semanticId.
  To ease writing, .keys[0].value can be left off for References.
  semanticId is defined as the "value" of the first key of the semanticId Reference object. The following two expressions are equivilant:
    - <somePath>.semanticId
    - <somePath>.semanticId.keys[0].value
- Implicite casting is used together with FieldIdentifiers. FieldIdentifiers are generally treated as xs:string in the query language.
- If a FieldIdentifier is used in a logicalExpression, it will be implicitely casted to xs:boolean, which can only create a valid result for the values true and false.
- If a FieldIdentifier is used in a comparison, the second parameter decides implicite casting. If the second parameter is a constant (string, number, hex, boolean, dateTime, time) or a corresponding explicite casting operator, the value of the FieldIdentifier will be implicitely casted to the corresponding data type.
- A recursive search is made through the Submodel Elements, when the optional IdShortPath is left out.

# AAS Query Language to Cypher Query

AAS Query (Written in AAS Query Language) -> Cypher Query
Cypher Query -> Neo4j -> Neo4J Result
Neo4J Result -> AAS Query Result

## Examples

Note: The following examples are written in AAS Query Language. To see the corresponding Cypher Query, please check the folder `neo4aas/examples/queries`.

### Single comparison
```json
{
  "$condition": {
    "$eq": [
      { "$field": "$aas#idShort" },
      {
        "$field":
            "$aas#assetInformation.assetType"
      }
    ]
  }
}
```

### HandoverDocumentation with VDI 2770 Class 03-01 Commissioning and language NL (as expected with SubmodelElementList)

```json
{
  "$condition": {
    "$match": [
      { "$eq": [
          { "$field": "$sme.Documents[].DocumentClassification.Class#value" },
          { "$strVal": "03-01" }
        ]
      },
      { "$eq": [
          { "$field": "$sme.Documents[].DocumentVersion.SMLLanguages[]#language" },
          { "$strVal": "nl" }
        ]
      }
    ]
  }
}
```

### TechnicalData with motor starter (ECLASS ClassId = 27-37-09-05) and width less than 100 mm

```json
{
  "$condition": {
    "$and": [
      { "$match": [
          { "$eq": [
              { "$field": "$sm#idShort" },
              { "$strVal": "TechnicalData" }
            ]
          },
          { "$eq": [
              {
                "$field":
                "$sme.ProductClassifications.ProductClassificationItem.ProductClassId#value"
              },
              { "$strVal": "27-37-09-05" }
            ]
          }
        ]
      },
      { "$match": [
          {
            "$eq": [
              { "$field": "$sm#idShort" },
              { "$strVal": "TechnicalData" }
            ]
          },
          {
            "$eq": [
              { "$field": "$sme#semanticId" },
              { "$strVal": "0173-1#02-BAF016#006" }
            ]
          },
          {
            "$lt": [
              { "$field": "$sme#value" },
              { "$numVal": 100 }
            ]
          }
        ]
      }
    ]
  }
}
```
