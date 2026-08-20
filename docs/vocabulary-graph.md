# The vocabulary graph

`neo4aas vocab` loads what an AAS corpus *says* — element shapes, submodel structures,
concepts, IDTA templates and vendors — into Neo4j. It is a second importer beside the
instance importer in `core/serialization/`, and it shares nothing with it but the driver.

**One rule: no instance ever becomes a node.** No element nodes, no per-submodel element
subtrees, no `:Reference` node per occurrence. The instance importer does that, and it is
why it does not scale — the reference implementation landed 39 930 `:semanticId` edges on
68 nodes, which at 300 000 shells is a single node referenced 10–20 million times.

Everything here is measured against the reference corpus (8 vendors, 153 923 files,
~278 000 shells) with `scripts/bench/vocab_acceptance.sh`. The design argument and the
prior measurements it rests on are in
`discaas/docs/findings/13-redundancy-and-the-storage-unit.md`.

---

## The model

```
(:Shape     {shape_key, id_short, model_type, value_type, category, occurrences})
(:Structure {structure_key, element_count, slot_count, occurrences})
(:SemanticId{value, base, key_types, ref_types})
(:Concept   {irdi, irdi_base, coded_name, level, source, kind,
             preferred_name, definition, unit, data_type, deprecated})
(:Template  {template_id, family, version, status})        -- published | deprecated
(:Vendor    {name})

(:Shape)-[:DECLARES]->(:SemanticId)
(:SemanticId)-[:RESOLVES_TO]->(:Concept)      -- absent when unresolvable; that is data
(:Concept)-[:BROADER]->(:Concept)             -- ECLASS CodedName prefix hierarchy
(:Structure)-[:HAS_SLOT {ord, depth, parent_ord}]->(:Shape)
(:Template)-[:DECLARES_SHAPE]->(:Shape)
(:Vendor)-[:USES {occurrences}]->(:Shape)
(:Vendor)-[:SHIPS {submodels}]->(:Structure)
```

### Why each rule is there

**Keys are stable hashes over verbatim values.** `shape_key` is SHA-256 over the canonical
JSON of `[idShort, semanticId, modelType, valueType]`; `structure_key` is SHA-256 over the
ordered `shape_key` sequence. No normalisation, no case folding, no trimming — normalising
would merge two vocabularies that really are different. Both digests are pinned as literals
in `tests/vocabulary/test_extract.py`, because changing how a key is computed does not fail
a test somewhere: it orphans every node in every database that already holds this
vocabulary. Such a change is a migration.

**Every write is `MERGE`, with one deliberate exception.** Re-importing the same corpus must
not move a single count. That is the primary acceptance test, and it is what catches a
`CREATE` in the wrong place as well as a key that is not a pure function of its input.

The exception is the slot write. A structure's slots are `CREATE`d, not merged, in the *same
transaction* that sets the `slots_written` flag which suppresses them — so the pair is atomic,
a re-import writes nothing, and a failure mid-load leaves a structure either fully slotted or
not slotted at all. `MERGE` there is not merely slower, it is **quadratic**: merging a
relationship with properties re-reads the source node's relationship chain once per row, so a
3 000-slot structure costs about 4.5 M chain steps to write 3 000 edges, and the corpus has
tens of thousands of such structures. It is safe for a specific reason — `structure_key` *is*
the ordered slot sequence, so a structure's slots cannot change and there is never an existing
edge to merge with. The idempotence the rule exists to guarantee is preserved, and tested.

**semanticIds match on the value only and join on the version-stripped base.** `Key.type`
disagrees across vendors for identical values — one writes `ConceptDescription`, another
`GlobalReference` for the same IRDI — so both are recorded on the node and neither is used
for matching. Dropping the `#<version>` suffix lifts resolution roughly tenfold, because
ECLASS bumps the version on effectively every shared base between releases.

**Occurrence counters are properties, not edges.** An edge per occurrence is the super-node
this design exists to avoid. A counter and idempotence pull against each other (`c = c + n`
doubles on re-import), so the counter is **scoped to the vendor and replaced**: one run over
a vendor's files sets `(:Vendor)-[:USES]->(:Shape).occurrences` to what that run saw, and
`Shape.occurrences` is recomputed as the sum over vendors in `finalize()`. `--mode
accumulate` adds instead — for deliberately sharded loads — and is consequently not
idempotent.

**Unresolved semanticIds stay in the graph** with no `:RESOLVES_TO`. Roughly a third of them
resolve nowhere, and a vendor pointing at an identifier no dictionary defines is a finding,
not an ingest error. The report lists them.

**Shape co-occurrence is never materialised.** Two hops through `:Structure` give it;
materialising it is 3.6 M edges for one sample.

### Two deviations from the sketch, both measured

**A structure is the ordered shape sequence of a submodel's *elements*; the submodel's own
shape is not a slot.** It was, in the first version, on the argument that a head slot makes
a structure self-describing. It measurably broke the thing structures are for. Several
vendors put a per-product token in the submodel's `idShort` — SICK ships
`Nameplate_0226303791c8a2efe04ed3e83865b6b7` — so a head slot gives every *product* its own
structure: 584 structures with the head against 452 without on a 400-file sample, and the
gap widens with corpus size. A layout must not carry a serial number. The submodel's shape
is still recorded as a `:Shape` (the vendor uses it, and it declares the submodel's
semanticId); it simply is not part of the layout.

`element_count` is the number of elements at every depth — one `:HAS_SLOT` edge each.
`slot_count` is the number of top-level slots, i.e. how wide the submodel is before any
nesting.

**A property concept also gets a `:BROADER` edge to the ECLASS class that describes it.**
The sketch defines `:BROADER` as CodedName prefix containment, which only relates *classes*.
But most semanticIds in the corpus resolve to a *property*, and a property with no broader
concept has no siblings — so query expansion, the traversal the graph exists for, would have
nothing to expand through. The class→property links come from ECLASS's own `described_by`
block and are joined on the version-stripped base. `--no-property-class-edges` turns them off.

---

## Usage

```bash
# instances: shapes, structures, vendors
neo4aas vocab import --vendor-from-dir --workers 8 aas-corpus/instances/vendors

# IDTA templates (published *and* deprecated)
neo4aas vocab import --templates aas-corpus/specs/submodel-templates

# an ECLASS OntoML export, optionally a subset of segments
neo4aas vocab import --eclass .../1730150000deDEbasicXML01 --eclass-segments 27

# all three in one run, with a JSON report
neo4aas vocab import --vendor-from-dir --workers 8 \
    --templates aas-corpus/specs/submodel-templates \
    --eclass .../1730150000deDEbasicXML01 \
    --report-json report.json aas-corpus/instances/vendors

neo4aas vocab stats
neo4aas vocab clear            # asks; --yes to skip
```

Three loaders, each runnable alone and each idempotent. `--limit N` takes the first N files
in path order; add `--stratify` to spread those N round-robin across vendors, which is what
the growth measurements below use — 92 % of shapes are vendor-private, so the first N files
of one vendor say nothing about how the vocabulary grows.

Sources are read gzip- and BOM-transparently (`core.io` detects gzip by magic bytes; a bare
`glob("*.json")` over the corpus finds nothing, because every instance is on disk as
`x.json.gz`). JSON, XML and AASX all go through the same walk. A document that will not parse
is reported and skipped — ingestion never crashes on bad input.

---

## Acceptance

Reproduce with `scripts/bench/vocab_acceptance.sh {idempotence|tiers|full|traversals}`
against a throwaway Neo4j 5 (`URI` overrides the default `bolt://localhost:7688`). The runs
below are on a 10-core laptop, `--workers 8`, Neo4j 5 in Docker with a 4 GB heap.

### 1. Idempotence — the primary test

Import 1 000 files, then import the same 1 000 files again. Nothing may move.

```
===== 1. IDEMPOTENCE — import 1 000 files twice =====
NODES
  Shape                   3,535
  SemanticId              1,421
  Concept                   874
  Structure                 800
  Vendor                      8
  Template                    0
  TOTAL                   6,638   (128.5 elements per node)

EDGES
  HAS_SLOT              354,900
  USES                    3,859
  DECLARES                3,529
  RESOLVES_TO             1,028
  SHIPS                     801
  BROADER                     0
  DECLARES_SHAPE              0

SEMANTIC ID RESOLUTION
--- diff of run 1 against run 2 (empty means nothing moved) ---
(identical)
```

The same corpus re-imported by a *second loader in a second process* is also checked, in
`tests/vocabulary/test_load.py::test_reimport_by_a_second_loader_changes_nothing` — keys must
be stable across machines, not merely within one run — together with the counter semantics
(`test_accumulate_mode_adds_where_replace_mode_replaces`) and the slot-write skip
(`test_structure_slots_are_written_once`).

### 2. Sublinearity — node count against file count

Files are drawn round-robin across all eight vendors (`--stratify`), so every tier sees the
same vendor mix and the growth is not an artefact of meeting a new vendor.

```
===== 2. SUBLINEARITY — node count against file count =====
   files    elements    Shape Shape/no#  Structure  SemanticId  seconds
    1000      853035     3535       814        800        1421     22.7
   10000     7055942    11468      1104       4440        3217     78.8
   50000    28438821    48846      2256      19507        5593    291.8
```

Per-file rates, which is what the criterion is really about:

| files | elements/file | shapes/file | clean shapes/file | structures/file | elements per shape |
|---:|---:|---:|---:|---:|---:|
| 1,000 | 853 | 3.54 | 0.814 | 0.80 | 241 |
| 10,000 | 706 | 1.15 | 0.110 | 0.44 | 615 |
| 50,000 | 569 | 0.98 | 0.045 | 0.39 | 582 |
| 153,853 | 553 | 1.59 | 0.024 | 0.25 | 347 |

Shapes and structures both flatten — but not to a constant, and the reason is worth more
than the criterion.

**95 % of shapes carry a per-product token in the `idShort`.** A shape is keyed on the
`idShort` verbatim, which is right: normalising would merge vocabularies that genuinely
differ. But several vendors write product data into it — `OrdNo_1234567`, `Document07`,
SICK's `ZMN…` codes — and those shapes are not vocabulary. Each occurs exactly once, and the
shape count then tracks the *product* count rather than the schema. Every import reports it:

```
HOW MUCH OF THE VOCABULARY IS REALLY PER-PRODUCT IDENTITY
  6,633 of 248,325 shapes have an idShort with no 2+ digit run (2.7 %)
  241,692 carry a number — OrdNo_1234567, Document07, ZMN codes: product data written into the idShort
  214,113 occur exactly once in the whole corpus
    sick                211,363 shapes,  99 % numbered
    harting              23,475 shapes,  95 % numbered
    phoenix_contact       5,496 shapes,  98 % numbered
    festo                 3,145 shapes,  42 % numbered
    r_stahl                 881 shapes,  91 % numbered
    abb                     727 shapes,  94 % numbered
    buerkert                322 shapes,   6 % numbered
    wago                    272 shapes,   5 % numbered
```

Strip them and the vocabulary is what the design predicts. Shapes whose `idShort` carries
no number, against file count:

| files | clean shapes | per file |
|---:|---:|---:|
| 1 000 | 814 | 0.814 |
| 10 000 | 1 104 | 0.110 |
| 50 000 | 2 256 | 0.045 |
| 153 853 | 3 713 | 0.024 |

**A 34× fall in the per-file rate over a 154× growth in files**, ending at 3 713 shapes for
85 million elements — 23 000 elements per shape. That is the flattening the criterion asks
for. The instance side has not leaked in; the corpus contains vendors who write serial
numbers into their schema, and this graph is the first place that is visible corpus-wide.
(The 6 633 in the report above is the same figure after the IDTA templates are loaded, which
declare 3 094 further shapes that no vendor ships.)

### 3. No super-node

```
TOP-DEGREE NODES
   212,761  :Vendor       sick
   134,056  :Shape        AAQ373-Manufacturer
   134,054  :Shape        AAO676-Internal_article_number
   133,704  :Shape        AAW338-Manufacturer_product_designation
   128,009  :Shape        AAN350-Part_relation_to_the_main_device
   125,437  :Shape        AAO663-GTIN__EAN__code
   103,754  :Shape        AAO677-Manufacturer_name
    89,865  :Shape        AAN475-Number_of_parts_relations
    76,666  :Shape        AAO057-Product_type
    75,937  :Shape        AAN406-number_of_the_more-wire_conductors
```

High degree on a `:Shape`, `:SemanticId` or `:Concept` is the design working: 543× Reference
reuse has to land somewhere, and the whole point is that it lands on *one* node instead of
being repeated per occurrence. `AAQ373-Manufacturer` carries 134 056 edges and stands in for
188 779 elements; 134 054 of those edges are `:HAS_SLOT`, i.e. the positions it occupies
across the 39 168 distinct structures. Degree here is bounded by the *structure* vocabulary,
which is what §2 measures, not by the corpus:

```cypher
MATCH (s:Shape {id_short: 'AAQ373-Manufacturer'})
RETURN s.occurrences, count { (s)--() } AS degree,
       count { (:Structure)-[:HAS_SLOT]->(s) } AS slots
-- 188779 | 134056 | 134054
```

The top row needs saying out loud: `:Vendor sick` at degree 212 761 is the highest-degree
node in the graph, and it is one `:USES` edge per shape that vendor ships. It is bounded by
the *shape* count, not the element count — and it is 99 % numbered idShorts, so it is the
same finding as §2 seen from the other side. A vendor that names its fields consistently has
a few hundred: `wago` has 272.

What matters is that no degree anywhere comes from an instance edge — there are no instance
nodes to have one. `test_no_instance_ever_becomes_a_node` asserts that every label and every
relationship type in the database is one this importer owns.

### 4. Resolution rate

**69.7 %**, against the 68.7 % measured independently on this corpus — and 95.4 % if the
question is restricted to the semanticIds a vendor actually ships, which is what the earlier
figure counted. The dictionary here is ECLASS 15.0 BASIC DE (all 40 segments) plus the
ConceptDescriptions embedded in the AAS files themselves; on embedded CDs alone it is 52.8 %,
so the two sources are both load-bearing.

```
SEMANTIC ID RESOLUTION
  6,856 of 9,834 resolve into the dictionary (69.7 %)
  2,978 resolve nowhere — listed below, not hidden
  of those a vendor actually ships: 6,771 of 7,099 (95.4 %)
      146 shapes  0173-1#02-AAQ661#013
      130 shapes  0173-1#02-AAQ672#013
      128 shapes  0173-1#02-AAQ680#012
       96 shapes  0173-1#02-AAQ683#013
       69 shapes  0173-1#02-AAQ669#013
       60 shapes  0173-1#02-AAQ675#008
       51 shapes  0173-1#02-AAQ676#011
       35 shapes  0173-1#02-AAS425#008
       34 shapes  0173-1#02-AAQ535#007
       33 shapes  0173-1#02-AAQ688#012
       31 shapes  http://admin-shell.io/dexpi/1/0/SubTagMapping
       29 shapes  0173-1#02-AAQ671#011
       29 shapes  0173-1#02-AAS431#005
       29 shapes  0173-1#02-AAS435#006
       27 shapes  0173-1#02-ABI459#003
       21 shapes  https://admin-shell.io/idta/cds/attributeContainer/1
       20 shapes  0173-1#02-AAQ408#010
       15 shapes  0173-1#02-AAZ762#006
       15 shapes  https://admin-shell.io
       14 shapes  0173-1#02-AAQ673#013
       13 shapes  0173-1#02-AAQ536#007
       13 shapes  https://admin-shell.io/idta/HierarchicalStructures/Template/1/1/Submodel
       12 shapes  http://admin-shell.io/DEXPI/1/0/TagMapping
       11 shapes  0173-1#02-AAQ680#010
       11 shapes  0173-1#02-AAZ760#004
```

What the remaining 2 978 are, since "31 % resolve nowhere" is only useful if you know what
they are:

```cypher
MATCH (n:SemanticId) WHERE NOT (n)-[:RESOLVES_TO]->()
RETURN CASE WHEN n.value STARTS WITH '0173-1' THEN 'ECLASS IRDI'
            WHEN n.value STARTS WITH '0112'   THEN 'IEC CDD IRDI'
            WHEN n.value STARTS WITH 'http'   THEN 'URL (IDTA / vendor)'
            ELSE 'other' END AS kind, count(*) AS n ORDER BY n DESC
```
```
  URL (IDTA / vendor)   2277
  ECLASS IRDI            411
  other                  244
  IEC CDD IRDI            46
```

Three quarters are IDTA or vendor-defined URLs, which no dictionary defines by construction —
`https://admin-shell.io/idta/nameplate/3/0/Nameplate` is a template identifier, not a concept.
46 are IEC CDD IRDIs from a dictionary that is not loaded. The 411 ECLASS IRDIs are the
interesting ones: their version-stripped base is absent from ECLASS 15.0 entirely
(`0173-1#02-AAQ661`, `0173-1#02-AAS425` and their neighbours return zero concepts on
`irdi_base`), so those are vendors citing identifiers that the current release has retired.
That is a data-quality finding, and it is one query.

### 5. The six traversals

`scripts/bench/vocab_traversals.py` runs all six against the full graph and prints each with
its timing. Every one is a single Cypher pattern; in SQL each is a recursive CTE plus four or
five joins, re-planned per query. None of them touches an instance.

What they show, in one line each:

1. **Cross-vendor field mapping.** `harting.cAxBasic` and `phoenix_contact.ADN292-CAx_basic`
   are the same concept. 92 % of shapes are vendor-private; the concept is the only bridge.
2. **Query expansion.** From `RatedVoltage`, through ECLASS, to `BAH005-rated_voltage`,
   `Bemessungsspannung` and `ratedVoltage` — one concept, four spellings, four vendors.
3. **Junk detection.** `https://admin-shell.io/idta/nameplate/3/0/Nameplate` is declared by
   54 035 distinct shapes. Today that is a batch job; here it is a node degree, free and
   never stale.
4. **Faceting.** What else is on a thing that has a `ManufacturerName`: `ManufacturerOrderCode`
   in 17 201 structures, `ManufacturerProductDesignation` in 17 049. Materialising this is
   3.6 M pairs; traversing it is 1.5 s.
5. **Undeclared templates.** 24 324 structures covering 124 462 submodels overlap no IDTA
   template — with `Document00_STEP` (wago, 23 227 submodels) and `IdentifyingOrderNumber`
   (wago, 18 466) at the top. These are de-facto templates nobody published.
6. **Deprecated usage.** Every vendor ships superseded ECLASS versions — phoenix_contact 1 651
   stale identifiers, festo 345, sick 273 — and SICK's nameplates still match the *deprecated*
   `ZVEI_Digital_Nameplate 1.0.0`. This is why `deprecated/` is read.

```
------------------------------------------------------------------------------
1. CROSS-VENDOR FIELD MAPPING
  two vendors, two names, one concept — 92 % of shapes are vendor-private and this is the only bridge
  530.4 ms, 10 rows
  concept               name                                vendor_a  field_a                           vendor_b         field_b                                       
  0173-1#01-ADN228#012  Identification                      harting   identification                    phoenix_contact  ADN228-Identification                         
  0173-1#01-ADN292#012  CAx basic                           harting   cAxBasic                          phoenix_contact  ADN292-CAx_basic                              
  0173-1#01-ADN293#013  CAx connector and function          harting   cAxConnectorAndFunction           phoenix_contact  ADN293-CAx_connector_and_function             
  0173-1#01-ADN329#008  Information                         harting   information                       phoenix_contact  ADN329-Information                            
  0173-1#01-ADN464#011  Add on Documentation                harting   addOnDocumentation                phoenix_contact  ADN464-Attached_Information                   
  0173-1#01-AGG434#005  Permissible environment conditions  harting   permissibleEnvironmentConditions  phoenix_contact  AGG434-Permissible_environment_conditions     
  0173-1#01-AHF578#001  Handover documentation              abb       HandoverDocumentation             sick             HandoverDocumentation_00006741583849946f19822…
  0173-1#01-AHF578#001  Handover documentation              abb       HandoverDocumentation             sick             HandoverDocumentation_00040bb7a5ab1b471a7d649…
  0173-1#01-AHF578#001  Handover documentation              abb       HandoverDocumentation             sick             HandoverDocumentation_0007db6a6113f9a8762c533…
  0173-1#01-AHF578#001  Handover documentation              abb       HandoverDocumentation             sick             HandoverDocumentation_00091709e063c46359415bf…

------------------------------------------------------------------------------
2. QUERY EXPANSION from 'voltage'
  term -> shape -> concept -> ECLASS siblings -> shapes
  897.7 ms, 10 rows
  from_field    via_class                  field                             name                                  in_eclass_classes  occurrences
  RatedVoltage  Grundpaket Lüftungssystem  RatedVoltage                      Rated Voltage                         20                 15971      
  RatedVoltage  Grundpaket Lüftungssystem  BAC859-Power_frequency            Netzfrequenz                          61                 20         
  RatedVoltage  Grundpaket Lüftungssystem  AAQ328-AWG_number                 AWG-Nummer                            112                20         
  RatedVoltage  Grundpaket Lüftungssystem  AAQ328-AWG-number                 AWG-Nummer                            112                4          
  RatedVoltage  Grundpaket Lüftungssystem  BAH005-rated_voltage              Bemessungsspannung                    136                2324       
  RatedVoltage  Grundpaket Lüftungssystem  Bemessungsspannung                Bemessungsspannung                    136                146        
  RatedVoltage  Grundpaket Lüftungssystem  ratedVoltage                      Bemessungsspannung                    136                7          
  RatedVoltage  Grundpaket Lüftungssystem  ContainsSubstanceAccordingToSvhc  Contains substance according to SVHC  19161              229        
  RatedVoltage  Grundpaket Lüftungssystem  CountryOfCustomsTariffNumber      Country of customs tariff number      36577              15         
  RatedVoltage  Grundpaket Lüftungssystem  countryOfCustomsTariffNumber      Country of customs tariff number      36577              1          

------------------------------------------------------------------------------
3. JUNK DETECTION
  :SemanticId by :DECLARES in-degree — a node degree, not a batch job
  311.7 ms, 10 rows
  semantic_id                                     shapes  distinct_id_shorts
  https://admin-shell.io/idta/nameplate/3/0/Nam…  54035   54035             
  0173-1#01-AHX837#002                            54034   54034             
  0173-1#02-AAQ837#005                            54033   54033             
  0173-1#01-AHF578#003                            48883   48883             
  https://admin-shell.io/idta/HierarchicalStruc…  22274   22274             
  0173-1#02-ABI500#001/0173-1#01-AHF579#001*01    1804    1804              
  0173-1#02-AAY811#001                            276     275               
  0173-1#02-AAQ661#013                            146     145               
  0173-1#02-ABI500#001/0173-1#01-AHF579#001       138     138               
  0173-1#02-AAQ672#013                            130     129               

------------------------------------------------------------------------------
4. FACETING around 'ManufacturerName'
  shape -> structure -> shape; materialising this is 3.6 M pairs
  1492.3 ms, 10 rows
  also_present                    model_type                 structures
  GeneralInformation              SubmodelElementCollection  17205     
  ManufacturerOrderCode           Property                   17201     
  ManufacturerProductDesignation  MultiLanguageProperty      17049     
  ManufacturerArticleNumber       Property                   16869     
  ManufacturerLogo                File                       15850     
  TechnicalProperties             SubmodelElementCollection  15848     
  ProductImage                    File                       11184     
  Materials                       SubmodelElementCollection  10350     
  Weight                          Property                   10239     
  Type                            MultiLanguageProperty      9963      

------------------------------------------------------------------------------
5. UNDECLARED TEMPLATES (summary)
  structures whose shape set overlaps no IDTA template
  642.7 ms, 2 rows
  declared  structures  submodels
  False     24324       124462   
  True      14844       599742   

------------------------------------------------------------------------------
5b. UNDECLARED TEMPLATES (the biggest)
  de-facto templates the industry uses and nobody published
  658.1 ms, 10 rows
  first_element           elements  top_level  submodels  vendors    
  Document00_STEP         20        1          23227      ['wago']   
  IdentifyingOrderNumber  3         3          18466      ['wago']   
  Document00_STEP         20        1          17201      ['harting']
  IdentifyingOrderNumber  1         1          2894       ['wago']   
  ProductGroup            4         4          1528       ['wago']   
  ArcheType               262       2          1008       ['harting']
  ArcheType               8         2          401        ['harting']
  ArcheType               3         2          368        ['harting']
  ContactInformation00    43        2          352        ['wago']   
  IdentifyingOrderNumber  3         3          351        ['wago']   

------------------------------------------------------------------------------
6. DEPRECATED USAGE — superseded ECLASS releases
  vendor -> shape -> concept -> a newer release of the same base
  41.4 ms, 7 rows
  vendor           stale_identifiers  examples                                      
  phoenix_contact  1651               ['0173-1#02-AAO677#002', '0173-1#02-AAW338#00…
  festo            345                ['0173-1#02-AAQ326#002', '0173-1#02-AAP906#00…
  sick             273                ['0173-1#02-AAQ326#002', '0173-1#02-AAU732#00…
  harting          249                ['0173-1#02-AAQ326#002', '0173-1#02-AAP906#00…
  wago             69                 ['0173-1#02-AAQ326#002', '0173-1#02-AAO677#00…
  r_stahl          14                 ['0173-1#02-AAQ326#002', '0173-1#02-AAO677#00…
  abb              2                  ['0173-1#02-AAO895#003', '0173-1#02-AAQ326#00…

--------------------------------------------------------ED USAGE — deprecated IDTA templaED USAGE — deprecated IDTA templates
  why deprecated/ is read: skipping it costs ~75 000 matches
  2.8 ms, 10 rows
  vendor           deprecated_template     version  shapes  occurrences
  sick             ZVEI_Digital_Nameplate  1.0.0    8       47332908   
  sick             Digital nameplate       2.0.0    10      19668012   
  harting          ZVEI_Digital_Nameplate  1.0.0    22      10991586   
  abb              ZVEI_Digital_Nameplate  1.0.0    5       6116910    
  harting          Digital nameplate       2.0.0    52      4651883    
  festo            ZVEI_Digital_Nameplate  1.0.0    22      3654645    
  abb              Digital nameplate       2.0.0    10      2869474    
  r_stahl          ZVEI_Digital_Nameplate  1.0.0    11      2121402    
  wago             ZVEI_Digital_Nameplate  1.0.0    15      1881387    
  phoenix_contact  Handover Documentation  1.2.0    13      1808076
```

### 6. Full corpus

153 853 files / 153 852 shells / 724 204 submodels / **85 025 569 elements**, plus 194 IDTA
templates and ECLASS 15.0 BASIC DE (all 40 segments), in **16 min 12 s** end to end
(829 s + 2 s + 141 s across the three loaders):

| | |
|---|---:|
| nodes | **418 543** |
| edges | **12 735 100** |
| elements per node | 203 |
| documents that failed to parse | **1** |

The one failure is a genuinely corrupt file — `buerkert/…_20260401_112138CET.xml.gz`, XML junk
after the document element at line 4505 — reported by path and error, skipped, and the other
153 852 loaded. That is the contract: ingestion never crashes on bad input.

Where the time goes is worth noting, because it says what this importer is bounded by. The
instance run spends 692 s extracting 85 M elements in 8 worker processes and 137 s writing
7.1 M `:HAS_SLOT` edges; every other write in the whole corpus is under six seconds, because
every other write is proportional to the *vocabulary*. The dictionary's 108 s is 2.7 M
ECLASS class→property edges — a property of ECLASS, not of the corpus.

```
===== 3. FULL CORPUS =====

--- instances: 153 853 files, wall clock 829.2 s ---

========================================================================
vocabulary import
========================================================================
wall clock               829.2 s
files                  153,853
documents parsed       153,852
documents failed             1
shells seen            153,852
submodels seen         724,204
elements seen         85,025,569
vendors               abb, buerkert, festo, harting, phoenix_contact, r_stahl, sick, wago

NODES
  Shape                 245,231
  Structure              39,168
  SemanticId              7,099
  Concept                 3,184
  Vendor                      8
  Template                    0
  TOTAL                 294,690   (288.5 elements per node)

EDGES
  HAS_SLOT            7,085,871
  USES                  245,681
  DECLARES              245,132
  SHIPS                  39,170
  RESOLVES_TO             4,233
  BROADER                     0
  DECLARES_SHAPE              0

WRITES
  HAS_SLOT              7,085,871 rows   137.25 s
  Shape                   245,231 rows     5.91 s
  USES                    245,681 rows     4.66 s
  DECLARES                245,132 rows     3.65 s
  Shape.occurrences             0 rows     1.41 s
  SHIPS                    39,170 rows     0.75 s
  Structure                39,168 rows     0.65 s
  Structure.occurrences          0 rows     0.20 s
  SemanticId                7,099 rows     0.18 s
  Concept                   3,184 rows     0.14 s
  RESOLVES_TO                   0 rows     0.04 s

FAILED DOCUMENTS (first 1)
  ParseError: junk after document element: line 4505, column 14
    /Users/igorgarmaev/PycharmProjects/aas-corpus/instances/vendors/buerkert/loose/Burkert_0142_000067684_type_20260401_112138CET.xml.gz
========================================================================

--- templates: 194 templates, wall clock 1.9 s ---

NODES
  Shape                 248,325
  Structure              39,168
  SemanticId              9,834
  Concept                 3,184
  Template                  194
  Vendor                      8

--- dictionary: ECLASS 15.0 BASIC DE, all 40 segments, wall clock 140.7 s ---

========================================================================
vocabulary import
========================================================================
wall clock               140.7 s
files                        0
documents parsed             0
documents failed             0
shells seen                  0
submodels seen               0
elements seen                0

NODES
  Shape                 248,325
  Concept               121,014
  Structure              39,168
  SemanticId              9,834
  Template                  194
  Vendor                      8

EDGES
  HAS_SLOT            7,085,871
  BROADER             5,098,901
  DECLARES              248,160
  USES                  245,681
  SHIPS                  39,170
  DECLARES_SHAPE          8,664
  RESOLVES_TO             8,653

WRITES
  BROADER(property)     2,706,281 rows   107.67 s
  Concept                 124,803 rows     7.44 s
  BROADER(prefix)          48,261 rows     1.47 s
  Shape.occurrences             0 rows     0.62 s
  RESOLVES_TO                   0 rows     0.17 s
  Structure.occurrences          0 rows     0.06 s
========================================================================
```

---

## What this deliberately is not

- **Not a store of values, elements, submodels or shells.** If it varies per product it does
  not belong here. The instance importer keeps those, verbatim, and this graph is a
  projection that can be dropped and rebuilt in one pass.
- **Not a replacement for the instance importer.** It is additive; nothing in
  `core/serialization/` changed.
- **Not ranking, text search or embeddings.** The graph decides *what to look for*; the
  relational store decides *where it is*.
- **Not document deduplication.** Measured at 1.02× against per-document compression — the
  compressor already collects it, for free, with no hash index and no garbage collection.
  Whole submodels are 2.0 % byte-identical; there is nothing there to factor out.
- **Not materialised co-occurrence.** `:Structure` is the join.

## Not built: the PostgreSQL fast path

The brief allows reading `submodel.doc` from a populated `discaas` PostgreSQL instead of
parsing files. It is not built, and the reason it is cheap to add later is the reason it was
not needed now: `extract.py` reaches the filesystem through exactly one function,
`iter_environments(path) -> Iterator[dict]`. A Postgres source is another producer of the
same dicts feeding the same fold, so it is a source swap and nothing else — no change to the
keys, the records, or the loader. Parsing the corpus directly costs about 4 ms per file and
is not the bottleneck.

## Known limits

- **`occurrences` is scoped to a vendor and replaced.** Loading disjoint shards of one vendor
  in separate runs needs `--mode accumulate`, which is not idempotent. There is no per-run
  ledger, because a ledger keyed on documents would be a node per document.
- **`:Concept` is flat with respect to corpus size but proportional to dictionary size.** A
  full ECLASS export is hundreds of thousands of concepts whether the corpus is 3 000 shells
  or 300 000; `--eclass-segments` narrows it to the segments a corpus actually uses.
- **Structure identity is exact, not fuzzy.** A submodel with seven documents and one with
  eight are different structures. That is the right primitive for "same layout", and the
  wrong one for "same family" — clustering structures into de-facto template families is the
  obvious next thing and is not built.
- **`Shape.category` is the smallest value seen** when two occurrences of one shape disagree.
  Deterministic, so a re-import is stable, but it is a choice rather than a fact.
- **The full corpus ships 8 470 products twice**, once as `.aasx` and once as `.xml`/`.json`
  — abb 2 486, buerkert 5 017, r_stahl 967, matched by file stem:

  ```
  $ find abb -name '*.aasx.gz' | sed 's|.*/||; s|\.aasx\.gz$||' | sort > a
  $ find abb -name '*.xml.gz'  | sed 's|.*/||; s|\.xml\.gz$||'  | sort > b
  $ comm -12 a b | wc -l
  2486
  ```

  Distinct counts are unaffected — that is the whole point of a vocabulary graph — but the
  occurrence counters double for those three vendors. Deduplicating documents is explicitly
  out of scope, so this is reported rather than fixed.
