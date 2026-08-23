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

Layout is **edges between shapes**, not a node per layout.

```
(:Shape     {shape_key, id_short, model_type, value_type, category,
             occurrences, top_level})
(:SemanticId{value, base, key_types, ref_types})
(:Concept   {irdi, irdi_base, coded_name, level, source, kind,
             preferred_name, definition, unit, data_type, deprecated})
(:Template  {template_id, family, version, status})        -- published | deprecated
(:Vendor    {name})

(:Shape)-[:DECLARES]->(:SemanticId)
(:SemanticId)-[:RESOLVES_TO]->(:Concept)      -- absent when unresolvable; that is data
(:Concept)-[:BROADER]->(:Concept)             -- ECLASS CodedName prefix / CDD superclass
(:Template)-[:DECLARES_SHAPE]->(:Shape)
(:Vendor)-[:USES {occurrences, top_level}]->(:Shape)

(:Shape)-[:CONTAINS {vendor_names[], vendor_occurrences[], vendor_submodels[],
                     occurrences, submodels, vendors, in_template}]->(:Shape)
```

### Layout is a merge, and the merge is lossy

An earlier version of this importer stored a node per distinct layout,
`(:Structure)-[:HAS_SLOT {ord, depth, parent_ord}]->(:Shape)`, one edge per slot.
Merging every submodel's layout into `(:Shape)-[:CONTAINS]->(:Shape)` is far smaller —
**7 085 871 slot edges become 51 665** on the full corpus, a 137x reduction, and the write
that took 137 s takes 1.5 s — and it is a *union*, so it loses its members:

* it cannot enumerate layouts. Of 1 448 parent shapes, **279 (19 %) have more than one
  distinct child set** and the worst has **402**. A path read off the merged graph may
  correspond to no real submodel.
* `(:Vendor)-[:SHIPS]->(:Structure)`, "how many submodels have this layout", and
  classifying an unknown submodel against known layouts all go with it.

The trade is deliberate. What is added back is **support**, and it is what makes the
merge usable rather than merely small.

### Support: two numbers, per vendor

Every containment edge records how many *element occurrences* and how many *submodels*
it was seen in — a pair seen in 12 000 submodels is schema, one seen in three is noise
— and it records both **per vendor**:

```
(:Shape)-[:CONTAINS {
    vendor_names:       ['festo', 'wago'],
    vendor_occurrences: [ 18422,   9013 ],
    vendor_submodels:   [  6140,   3004 ],
    occurrences: 27435, submodels: 9144,   -- reduce() sums, recomputed in finalize()
    in_template: true                      -- an IDTA template declares this pair
}]->(:Shape)
```

Eight vendors means eight slots. It costs almost nothing and it buys two things:

1. **"Which vendor nests this under that, and how often"** — the vendor dimension the
   merge would otherwise destroy.
2. **Idempotence.** This is the part that is not optional. A `:CONTAINS` edge is shared
   by every vendor that ships the pair, so a run cannot *replace* the number (it would
   erase the other vendors) and cannot *accumulate* it (it would double on re-import).
   Each run replaces its own array slot and nothing else. `finalize()` recomputes the
   scalar totals with `reduce()` over the arrays, so a total can never drift from the
   slots it summarises.

### Why each of the other rules is there

**Keys are stable hashes over verbatim values.** `shape_key` is SHA-256 over the canonical
JSON of `[idShort, semanticId, modelType, valueType]`. No normalisation, no case folding,
no trimming — normalising would merge two vocabularies that really are different. The
digest is pinned as a literal in `tests/vocabulary/test_extract.py`, because changing how
a key is computed does not fail a test somewhere: it orphans every node in every database
that already holds this vocabulary. Such a change is a migration.

**Every write is `MERGE`.** Re-importing the same corpus must not move a single count.
That is the primary acceptance test, and it is what catches a `CREATE` in the wrong place
as well as a key that is not a pure function of its input.

**semanticIds match on the value only and join on the version-stripped base.** `Key.type`
disagrees across vendors for identical values — one writes `ConceptDescription`, another
`GlobalReference` for the same IRDI — so both are recorded on the node and neither is used
for matching. Dropping the `#<version>` suffix lifts resolution roughly tenfold, because
ECLASS bumps the version on effectively every shared base between releases.

**The Submodel's own shape is never a containment parent.** Several vendors put a
per-product token in the submodel `idShort` — SICK ships
`Nameplate_0226303791c8a2efe04ed3e83865b6b7` — so rooting containment there would give
every *product* its own subtree and defeat the merge entirely. Top-level elements are
counted instead, on `Shape.top_level` and `(:Vendor)-[:USES]->(:Shape).top_level`.

**Unresolved semanticIds stay in the graph** with no `:RESOLVES_TO`. Roughly a third of
them resolve nowhere, and a vendor pointing at an identifier no dictionary defines is a
finding, not an ingest error. The report lists them.

**Shape co-occurrence is never materialised.** Two hops through a shared `:CONTAINS`
parent give it; materialising it is 1.9 **billion** pairs (`Σ n²`) on the 5 000-file load
alone.

**A property concept also gets a `:BROADER` edge to the ECLASS class that describes it.**
The sketch defines `:BROADER` as CodedName prefix containment, which only relates
*classes*. But most semanticIds in the corpus resolve to a *property*, and a property with
no broader concept has no siblings — so query expansion, the traversal the graph exists
for, would have nothing to expand through. The class→property links come from ECLASS's own
`described_by` block and are joined on the version-stripped base.
`--no-property-class-edges` turns them off.

### Two dictionaries, one identifier space

ECLASS is not the only dictionary vendors point at. IEC CDD publishes IEC 61360-4/-7,
61987, 62683, 62720, 63213 and 63508 under the ICD `0112`, and the corpus ships it as one
JSONL file per standard. Both load into the same `:Concept` label, merged on `irdi` and
joined to semanticIds on the same version-stripped base, and they rank *equally* in
`SOURCE_PRECEDENCE` — safe only because the two ICDs never name the same concept, which
`test_the_two_dictionaries_do_not_contest_each_other` pins.

One thing IEC CDD does better: **hierarchy is explicit.** ECLASS encodes it positionally in
the 8-digit CodedName and this importer recovers it by prefix containment; the CDD export
names `superclass_irdi` outright. So a CDD concept carries no `coded_name` and there is no
prefix pass over it — `class_broader_pairs()` reads the reference. `higher_level_classes`
is deliberately ignored: it lists the *whole* ancestor chain, and emitting it would make
every ancestor a direct parent and flatten the hierarchy it describes.

Two defects of the export are decided at parse time rather than passed into the graph:

- **1 547 properties carry a relative IRDI** — scraped without their ICD prefix
  (`AAE930#001`). Such a value joins nothing, and the prefix could only be *guessed* from
  `published_in`. They are skipped and counted; 1 511 of them already exist in full form
  elsewhere in the export, so dropping them loses almost nothing, while inventing an
  identifier would poison the join key everything else rests on.
- **7 117 IRDIs are published more than once** with differing content, up to eight times,
  6 445 differing in `revision` — the CDD revises inside a version. The highest revision
  wins, ties broken on the CDD's own `unid`, so the pick cannot depend on the order lines
  were read. Without that, two imports of one export would disagree.

`status_level` is `Superseded` for half the export. That marks an older *version* of a live
concept, not a withdrawn one, so only the explicit `is_deprecated` flag sets
`Concept.deprecated`.

### Upgrading from the `:Structure` model

`vocab clear` removes `:Structure`, `:HAS_SLOT` and `:SHIPS` as well as the current labels
(`schema.LEGACY_LABELS` / `LEGACY_RELATIONSHIPS`), so moving a database across is one
`clear` and one re-import.

## Usage

```bash
# instances: shapes, structures, vendors
neo4aas vocab import --vendor-from-dir --workers 8 aas-corpus/instances/vendors

# IDTA templates (published *and* deprecated)
neo4aas vocab import --templates aas-corpus/specs/submodel-templates

# an ECLASS OntoML export, optionally a subset of segments
neo4aas vocab import --eclass .../1730150000deDEbasicXML01 --eclass-segments 27

# the IEC CDD export: a directory of .jsonl dictionaries, or a single one
neo4aas vocab import --iec-cdd aas-corpus/dictionaries/iec_cdd

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

The runs pasted below were captured **before `:PRECEDES` was removed** and are kept as the
historical record rather than hand-edited. The instance load in them ran with the then-existing
`--precedes` flag, so every report still lists a `:PRECEDES` edge count and its write time; read
the current model by subtracting those lines. Nothing else moves — `:PRECEDES` was a separate
edge type written from the same walk, and it fed nothing.

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
  Vendor                      8
  Template                    0
  TOTAL                   5,838   (146.1 elements per node)

EDGES
  CONTAINS                7,112
  USES                    3,859
  DECLARES                3,529
  RESOLVES_TO             1,028
  BROADER                     0
  PRECEDES                    0
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
   files    elements    Shape Shape/no#   CONTAINS  SemanticId  seconds
    1000      853035     3535       814       7112        1421     15.8
   10000     7055942    11468      1104      12868        3217     66.3
   50000    28438821    48846      2256      31410        5593    280.0
```

Per-file rates, which is what the criterion is really about:

| files | elements/file | shapes/file | clean shapes/file | CONTAINS/file | elements per shape |
|---:|---:|---:|---:|---:|---:|
| 1 000 | 853 | 3.54 | 0.814 | 7.11 | 241 |
| 10 000 | 706 | 1.15 | 0.110 | 1.29 | 615 |
| 50 000 | 569 | 0.98 | 0.045 | 0.63 | 582 |
| 153 853 | 553 | 1.59 | 0.024 | 0.34 | 347 |

Containment flattens hardest of all — **7.11 edges per file at 1 000 files, 0.34 at 153 853**,
a 21x fall — which is the merge doing its job: new documents mostly repeat nesting that is
already an edge.

Shapes flatten too, but not to a constant, and the reason is worth more than the criterion.

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

**A 34x fall in the per-file rate over a 154x growth in files**, ending at 3 713 shapes for
85 million elements — 23 000 elements per shape. That is the flattening the criterion asks
for. The instance side has not leaked in; the corpus contains vendors who write serial
numbers into their schema, and this graph is the first place that is visible corpus-wide.
(The 6 633 in the report above is the same figure after the IDTA templates are loaded, which
declare 3 094 further shapes that no vendor ships.)

### 3. No super-node

```
TOP-DEGREE NODES
   211,363  :Vendor       sick
    54,036  :SemanticId   https://admin-shell.io/idta/nameplate/3/0/Nameplate
    54,035  :SemanticId   0173-1#01-AHX837#002
    54,034  :SemanticId   0173-1#02-AAQ837#005
    48,885  :SemanticId   0173-1#01-AHF578#003
    41,715  :Concept      0173-1#02-AAU731#002
    41,715  :Concept      0173-1#02-AAQ326#002
    41,715  :Concept      0173-1#02-AAW338#001
    41,715  :Concept      0173-1#02-AAO677#002
    41,715  :Concept      0173-1#02-AAU731#001
```

High degree on a `:Shape`, `:SemanticId` or `:Concept` is the design working: 543x Reference
reuse has to land somewhere, and the whole point is that it lands on *one* node instead of
being repeated per occurrence. Dropping the per-layout node changed the shape of this list —
the old model's top ten were all `:Shape` nodes carrying tens of thousands of `:HAS_SLOT`
edges, and those are gone. What remains is bounded by the *vocabulary*: a `:SemanticId` at
54 036 is one node for the 54 035 distinct shapes that declare it, which is the junk-detection
finding of traversal 3, not a scaling problem.

The top row needs saying out loud: `:Vendor sick` at 211 363 is the highest-degree node in the
graph, and it is one `:USES` edge per shape that vendor ships. Bounded by the shape count, not
the element count — and 99 % of those idShorts are numbered, so it is the same finding as §2
seen from the other side. A vendor that names its fields consistently has a few hundred: `wago`
has 272.

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
46 are IEC CDD IRDIs, which `--iec-cdd` now resolves. The 411 ECLASS IRDIs are the
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
4. **Faceting.** What else sits under the same parent as a `ManufacturerName`:
   `ManufacturerOrderCode`, `ManufacturerProductDesignation`, `ProductImages`, each in 54 037
   submodels. **One hop now, where the per-layout model needed two — 30 ms against 1 492 ms.**
   This is the clearest win from dropping `:Structure`.
5. **Where vendors agree.** The per-vendor arrays, read straight off the edge:
   `Phone → TelephoneNumber` is shipped by festo, harting and wago with
   `[30 362, 22 680, 23 227]` submodels each. This is the vendor dimension the merge would
   otherwise have destroyed, and it exists only because the support had to be per-vendor to be
   idempotent in the first place.
6. **Undeclared relations.** 47 003 containment relations that a vendor ships are declared by
   no IDTA template, against 173 that are — `DocumentVersion → OrganizationShortName` in
   70 712 submodels across harting and sick, published by nobody. The per-layout model could
   only answer this per *structure*; per relation is finer and more useful.
7. **Deprecated usage.** Every vendor ships superseded ECLASS versions — phoenix_contact
   1 651 stale identifiers, festo 345, sick 273 — and SICK's nameplates still match the
   *deprecated* `ZVEI_Digital_Nameplate 1.0.0`. This is why `deprecated/` is read.

```
===== 4. THE SIX TRAVERSALS =====

------------------------------------------------------------------------------
1. CROSS-VENDOR FIELD MAPPING
  two vendors, two names, one concept — 92 % of shapes are vendor-private and this is the only bridge
  516.9 ms, 10 rows
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
  630.2 ms, 10 rows
  from_field    via_class                  field                             name                                  in_eclass_classes  occurrences
  RatedVoltage  Grundpaket Lüftungssystem  RatedVoltage                      Rated Voltage                         20                 15971      
  RatedVoltage  Grundpaket Lüftungssystem  BAC859-Power_frequency            Netzfrequenz                          61                 20         
  RatedVoltage  Grundpaket Lüftungssystem  AAQ328-AWG_number                 AWG-Nummer                            112                20         
  RatedVoltage  Grundpaket Lüftungssystem  AAQ328-AWG-number                 AWG-Nummer                            112                4          
  RatedVoltage  Grundpaket Lüftungssystem  BAH005-rated_voltage              rated voltage                         136                2324       
  RatedVoltage  Grundpaket Lüftungssystem  Bemessungsspannung                rated voltage                         136                146        
  RatedVoltage  Grundpaket Lüftungssystem  ratedVoltage                      rated voltage                         136                7          
  RatedVoltage  Grundpaket Lüftungssystem  ContainsSubstanceAccordingToSvhc  Contains substance according to SVHC  19161              229        
  RatedVoltage  Grundpaket Lüftungssystem  CountryOfCustomsTariffNumber      Land der Zolltarifnummer              36577              15         
  RatedVoltage  Grundpaket Lüftungssystem  countryOfCustomsTariffNumber      Land der Zolltarifnummer              36577              1          

------------------------------------------------------------------------------
3. JUNK DETECTION
  :SemanticId by :DECLARES in-degree — a node degree, not a batch job
  290.0 ms, 10 rows
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
  one hop: shapes sharing a containment parent, ranked by support
  6.7 ms, 10 rows
  also_present                    model_type             submodels  vendors
  CompanyLogo                     File                   54037      2      
  ManufacturerArticleNumber       Property               54037      2      
  ManufacturerOrderCode           Property               54037      2      
  ManufacturerProductDesignation  MultiLanguageProperty  54037      2      
  ProductImages                   SubmodelElementList    54037      2      
  ManufacturerArticleNumber       Property               40330      3      
  ManufacturerArticleNumber       Property               40330      3      
  ManufacturerLogo                File                   40330      3      
  ManufacturerLogo                File                   40330      3      
  ManufacturerOrderCode           Property               40322      3      

------------------------------------------------------------------------------
4b. WHERE VENDORS AGREE
  the per-vendor support arrays: who nests this under that, how often
  23.5 ms, 10 rows
  parent              child                      vendors                                    submodels_each         submodels
  Phone               TelephoneNumber            ['festo', 'harting', 'wago']               [30362, 22680, 23227]  76269    
  Email               EmailAddress               ['festo', 'harting', 'wago']               [15181, 22650, 23227]  61058    
  ContactInformation  CityTown                   ['festo', 'harting', 'wago']               [15181, 22648, 23227]  61056    
  ContactInformation  Street                     ['festo', 'harting', 'wago']               [15181, 22648, 23227]  61056    
  ContactInformation  NationalCode               ['festo', 'harting', 'wago']               [15181, 22648, 23227]  61056    
  ContactInformation  Zipcode                    ['festo', 'harting', 'wago']               [15181, 22648, 23227]  61056    
  GeneralInformation  ManufacturerLogo           ['harting', 'phoenix_contact', 'r_stahl']  [22647, 15749, 1934]   40330    
  GeneralInformation  ManufacturerArticleNumber  ['harting', 'phoenix_contact', 'r_stahl']  [22647, 15749, 1934]   40330    
  GeneralInformation  ManufacturerOrderCode      ['harting', 'phoenix_contact', 'r_stahl']  [22647, 15749, 1926]   40322    
  GeneralInformation  ManufacturerName           ['harting', 'phoenix_contact', 'r_stahl']  [22647, 15740, 1926]   40313    

------------------------------------------------------------------------------
5. UNDECLARED RELATIONS (summary)
  containment a vendor ships that no IDTA template declares
  26.9 ms, 2 rows
  declared  relations  submodels
  False     47003      41236795 
  True      173        2641114  

------------------------------------------------------------------------------
5b. UNDECLARED RELATIONS (the biggest)
  de-facto structure the industry uses and nobody published
  28.8 ms, 10 rows
  parent           child                     model_type             submodels  vendors            
  DocumentVersion  OrganizationShortName     Property               70712      ['harting', 'sick']
  DocumentVersion  OrganizationOfficialName  Property               70712      ['harting', 'sick']
  DocumentVersion  StatusValue               Property               70712      ['harting', 'sick']
  DocumentVersion  StatusSetDate             Property               70709      ['harting', 'sick']
  DocumentVersion  KeyWords                  MultiLanguageProperty  60834      ['harting', 'wago']
  DocumentVersion  OrganizationName          Property               60834      ['harting', 'wago']
  DocumentVersion  Title                     MultiLanguageProperty  60834      ['harting', 'wago']
  DocumentVersion  DigitalFile               File                   60834      ['harting', 'wago']
  DocumentVersion  Language                  Property               60834      ['harting', 'wago']
  DocumentVersion  Summary                   MultiLanguageProperty  60834      ['harting', 'wago']

------------------------------------------------------------------------------
6. DEPRECATED USAGE — superseded ECLASS releases
  vendor -> shape -> concept -> a newer release of the same base
  27.2 ms, 7 rows
  vendor           stale_identifiers  examples                                      
  phoenix_contact  1651               ['0173-1#02-AAO677#002', '0173-1#02-AAW338#00…
  festo            345                ['0173-1#02-AAQ326#002', '0173-1#02-AAP906#00…
  sick             273                ['0173-1#02-AAQ326#002', '0173-1#02-AAU732#00…
  harting          249                ['0173-1#02-AAQ326#002', '0173-1#02-AAP906#00…
  wago             69                 ['0173-1#02-AAQ326#002', '0173-1#02-AAO677#00…
  r_stahl          14                 ['0173-1#02-AAQ326#002', '0173-1#02-AAO677#00…
  abb              2                  ['0173-1#02-AAO895#003', '0173-1#02-AAQ326#00…

------------------------------------------------------------------------------
6b. DEPRECATED USAGE — deprecated IDTA templates
  why deprecated/ is read: skipping it costs ~75 000 matches
  2.6 ms, 10 rows
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
templates and ECLASS 15.0 BASIC DE (all 40 segments), in **16 min 14 s** end to end
(851 s + 2 s + 121 s across the three loaders):

| | this model | the `:Structure` model |
|---|---:|---:|
| nodes | **379 375** | 418 543 |
| edges | **5 661 724** (5 720 148 as captured, less the 58 424 `:PRECEDES`) | 12 735 100 |
| layout edges | **51 665** `:CONTAINS` | 7 085 871 `:HAS_SLOT` |
| layout write time | **1.5 s** | 137.3 s |
| documents that failed to parse | **1** | 1 |

**The layout write went from 137 seconds to one and a half.** Nothing else in the instance
load moved, because everything else was already proportional to the vocabulary; the run is now
almost entirely extraction (851 s of it), and the graph is 45 % smaller.

What the merge cost, printed by every import rather than argued about:

```
CONTAINMENT — THE MERGED LAYOUT
  51,665 :CONTAINS edges replace a node per layout
  200 are shipped by more than one vendor
  4,439 were seen in exactly one submodel — support, not schema
  4,662 are declared by an IDTA template, of which 4,489 no vendor implements
  4,312 of 4,679 parents have more than one child (widest: 22,233) — the merge cannot say which
  combinations of those children actually co-occur; the support counters are what is left to rank them by.
```

**200 of 51 665 containment relations are shipped by more than one vendor**, and that number
is essentially flat across the tiers (212 at 1 000 files, 212 at 10 000, 225 at 50 000). The
cross-vendor overlap is a constant, not a fraction — the same 92 %-vendor-private finding seen
through the layout.

The one parse failure is a genuinely corrupt file — `buerkert/…_20260401_112138CET.xml.gz`,
XML junk after the document element at line 4505 — reported by path and error, skipped, and
the other 153 852 loaded. That is the contract: ingestion never crashes on bad input.

```
===== 3. FULL CORPUS =====

--- instances: 153 853 files, wall clock 853.1 s ---

========================================================================
vocabulary import
========================================================================
wall clock               850.5 s
files                  153,853
documents parsed       153,852
documents failed             1
shells seen            153,852
submodels seen         724,204
elements seen         85,025,569
vendors               abb, buerkert, festo, harting, phoenix_contact, r_stahl, sick, wago

NODES
  Shape                 245,231
  SemanticId              7,099
  Concept                 3,184
  Vendor                      8
  Template                    0
  TOTAL                 255,522   (332.8 elements per node)

EDGES
  USES                  245,681
  DECLARES              245,132
  PRECEDES               58,424
  CONTAINS               47,176
  RESOLVES_TO             4,233
  BROADER                     0
  DECLARES_SHAPE              0

WRITES
  Shape                   245,231 rows     9.44 s
  USES                    245,681 rows     5.63 s
  DECLARES                245,132 rows     4.37 s
  Shape counters                0 rows     2.50 s
  PRECEDES                 58,672 rows     1.55 s
  CONTAINS                 47,440 rows     1.52 s
  CONTAINS submodels            0 rows     0.92 s
  CONTAINS support              0 rows     0.50 s
  PRECEDES support              0 rows     0.45 s
  SemanticId                7,099 rows     0.40 s
  Concept                   3,184 rows     0.26 s
  RESOLVES_TO                   0 rows     0.08 s

FAILED DOCUMENTS (first 1)
  ParseError: junk after document element: line 4505, column 14
    /Users/igorgarmaev/PycharmProjects/aas-corpus/instances/vendors/buerkert/loose/Burkert_0142_000067684_type_20260401_112138CET.xml.gz
========================================================================

--- templates: 194 templates ---

NODES
  Shape                 248,325
  SemanticId              9,834
  Concept                 3,184
  Template                  194
  Vendor                      8

--- dictionary: ECLASS 15.0 BASIC DE, all 40 segments ---

========================================================================
vocabulary import
========================================================================
wall clock               120.8 s
files                        0
documents parsed             0
documents failed             0
shells seen                  0
submodels seen               0
elements seen                0

NODES
  Shape                 248,325
  Concept               121,014
  SemanticId              9,834
  Template                  194
  Vendor                      8

EDGES
  BROADER             5,098,901
  DECLARES              248,160
  USES                  245,681
  PRECEDES               58,424
  CONTAINS               51,665
  DECLARES_SHAPE          8,664

WRITES
  BROADER(property)     2,706,281 rows    89.07 s
  Concept                 124,803 rows     6.28 s
  BROADER(prefix)          48,261 rows     1.21 s
  Shape counters                0 rows     0.44 s
  CONTAINS support              0 rows     0.14 s
  PRECEDES support              0 rows     0.08 s
  CONTAINS submodels            0 rows     0.08 s
  RESOLVES_TO                   0 rows     0.08 s
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
- **Not materialised co-occurrence.** A shared `:CONTAINS` parent is the join.

## Not built: the PostgreSQL fast path

The brief allows reading `submodel.doc` from a populated `discaas` PostgreSQL instead of
parsing files. It is not built, and the reason it is cheap to add later is the reason it was
not needed now: `extract.py` reaches the filesystem through exactly one function,
`iter_environments(path) -> Iterator[dict]`. A Postgres source is another producer of the
same dicts feeding the same fold, so it is a source swap and nothing else — no change to the
keys, the records, or the loader. Parsing the corpus directly costs about 4 ms per file and
is not the bottleneck.

## Known limits

- **Support is scoped to a vendor and replaced.** Loading disjoint shards of one vendor in
  separate runs needs `--mode accumulate`, which is not idempotent. There is no per-run ledger,
  because a ledger keyed on documents would be a node per document.
- **The vendor arrays cap out at however many vendors a corpus has.** Eight here; a corpus with
  thousands of suppliers would want a `(:Vendor)-[:NESTS]->` reification instead, which is
  exactly the per-layout node this model removed — at that point the trade should be re-measured
  rather than assumed.
- **`:Concept` is flat with respect to corpus size but proportional to dictionary size.** A
  full ECLASS export is hundreds of thousands of concepts whether the corpus is 3 000 shells
  or 300 000; `--eclass-segments` narrows it to the segments a corpus actually uses.
- **The merged layout cannot enumerate layouts, and no amount of support fixes that.** 19 % of
  parent shapes branch, so "which combinations of these children actually co-occur" is
  unanswerable by construction. Support ranks relations; it does not reconstruct sets. If a
  question genuinely needs whole layouts — conformance scoring per submodel, near-duplicate
  detection, classifying an unknown submodel — it needs the relational store, or a per-layout
  node reintroduced deliberately.
- **`:CONTAINS` has no multiplicity.** How many `Document0N` sit under one `Documents`
  collection in a single instance is not recorded, only totals. It is one more pair of
  per-vendor arrays if it is ever needed.
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
