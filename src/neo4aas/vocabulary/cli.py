"""``neo4aas vocab`` — import, inspect and clear the vocabulary graph."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from neo4aas.vocabulary.extract import VocabularyRecords, collect_files, extract_paths
from neo4aas.vocabulary.load import VocabularyLoader
from neo4aas.vocabulary.report import RunReport, collect

logger = logging.getLogger(__name__)

DEFAULT_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_USER = os.environ.get("NEO4J_USER", "neo4j")
DEFAULT_PASSWORD = os.environ.get("NEO4J_PASSWORD", "12345678")


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--uri", default=DEFAULT_URI, help="Neo4j bolt URI")
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE"))
    parser.add_argument("--batch-size", type=int, default=10_000)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="neo4aas vocab",
        description="Load the vocabulary of an AAS corpus into Neo4j: element "
                    "shapes, submodel structures, concepts, templates and vendors. "
                    "No instance ever becomes a node.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="load instances, templates and/or a dictionary")
    imp.add_argument("paths", nargs="*", type=Path,
                     help="AAS files or directories (gzipped files included)")
    imp.add_argument("--vendor-from-dir", action="store_true",
                     help="vendor is the first directory below each given path")
    imp.add_argument("--vendor", default="unknown",
                     help="vendor name when --vendor-from-dir is not used")
    imp.add_argument("--workers", type=int, default=1,
                     help="extraction worker processes (extraction is CPU-bound)")
    imp.add_argument("--limit", type=int, help="only the first N files, sorted")
    imp.add_argument("--stratify", action="store_true",
                     help="with --limit, spread the N files round-robin across vendors")
    imp.add_argument("--templates", type=Path,
                     help="root of the submodel-templates tree (published + deprecated)")
    imp.add_argument("--eclass", type=Path, help="root of an ECLASS OntoML export")
    imp.add_argument("--eclass-segments", default="",
                     help="comma-separated ECLASS segment numbers, e.g. 27,13")
    imp.add_argument("--iec-cdd", type=Path,
                     help="IEC CDD export: a directory of .jsonl dictionaries, or one file")
    imp.add_argument("--no-property-class-edges", action="store_true",
                     help="skip the property->class :BROADER edges from ECLASS")
    imp.add_argument("--mode", choices=("replace", "accumulate"), default="replace",
                     help="occurrence counters: replace the vendor's scope (idempotent) "
                          "or add to it (for disjoint shards; not idempotent)")
    imp.add_argument("--report-json", type=Path, help="also write the report as JSON")
    imp.add_argument("--dry-run", action="store_true",
                     help="extract and report, write nothing")
    add_connection_args(imp)

    stats = sub.add_parser("stats", help="counts, resolution rate and top-degree nodes")
    stats.add_argument("--report-json", type=Path)
    add_connection_args(stats)

    clear = sub.add_parser("clear", help="delete every node this importer owns")
    clear.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    add_connection_args(clear)
    return parser


def _driver(args):
    import neo4j

    # A count query for a label or relationship type that does not exist yet is a
    # legitimate question — "how many :Template nodes?" before any are loaded — and
    # the server answers it with a warning notification per query. Off, so the
    # report is not buried in them.
    return neo4j.GraphDatabase.driver(args.uri, auth=(args.user, args.password),
                                      notifications_min_severity="OFF")


def _loader(args, driver) -> VocabularyLoader:
    return VocabularyLoader(driver, database=args.database,
                            batch_size=args.batch_size,
                            mode=getattr(args, "mode", "replace"))


def _progress(report: RunReport, every: int = 2000):
    state = {"last": 0}

    def tick(records: VocabularyRecords) -> None:
        if records.documents - state["last"] >= every:
            state["last"] = records.documents
            print(f"  … {records.documents:,} documents, "
                  f"{len(records.shapes):,} shapes, "
                  f"{len(records.contains):,} containment pairs", file=sys.stderr)

    return tick


def command_import(args) -> int:
    report = RunReport()
    started = time.perf_counter()

    records = VocabularyRecords()
    if args.paths:
        report.files = len(collect_files(args.paths, args.limit, args.stratify))
        print(f"extracting {report.files:,} files …", file=sys.stderr)
        records = extract_paths(args.paths, vendor_from_dir=args.vendor_from_dir,
                                vendor=args.vendor, limit=args.limit,
                                workers=args.workers, stratify=args.stratify,
                                progress=_progress(report))

    templates = []
    if args.templates:
        print(f"reading templates from {args.templates} …", file=sys.stderr)
        from neo4aas.vocabulary.templates import extract_templates

        templates, records = extract_templates(args.templates, records)

    cdd_result = None
    if args.iec_cdd:
        from neo4aas.vocabulary.iec_cdd import parse_iec_cdd

        print(f"parsing IEC CDD from {args.iec_cdd} …", file=sys.stderr)
        cdd_result = parse_iec_cdd([args.iec_cdd])
        print(f"  {len(cdd_result.records):,} entries "
              f"({', '.join(f'{k}: {v:,}' for k, v in sorted(cdd_result.kinds.items()))}); "
              f"{cdd_result.skipped_relative:,} skipped for a relative IRDI, "
              f"{cdd_result.superseded_records:,} superseded by a higher revision",
              file=sys.stderr)
        records.failures.extend(cdd_result.failures)

    eclass_result = None
    if args.eclass:
        from neo4aas.vocabulary.concepts import parse_eclass

        segments = [int(s) for s in args.eclass_segments.split(",") if s.strip()]
        print(f"parsing ECLASS from {args.eclass} "
              f"(segments: {segments or 'all'}) …", file=sys.stderr)
        eclass_result = parse_eclass(str(args.eclass), segments or None)

    report.documents = records.documents
    report.failed_documents = len(records.failures)
    report.failures = records.failures[:50]
    report.shells = records.shells
    report.submodels = records.submodels
    report.elements = records.elements
    report.vendors = sorted(records.vendors)

    if args.dry_run:
        concepts = set(records.concepts)
        if cdd_result is not None:
            concepts.update(cdd_result.records)
        if eclass_result is not None:
            from neo4aas.vocabulary.concepts import concepts_from_eclass

            concepts.update(row["irdi"] for row in concepts_from_eclass(eclass_result))
        report.nodes = {
            "Shape": len(records.shapes),
            "SemanticId": len(records.semantic_ids), "Concept": len(concepts),
            "Template": len(templates), "Vendor": len(records.vendors),
        }
        report.relationships = {
            "CONTAINS": len({(p, c) for _, p, c in records.contains}),
            "DECLARES_SUPPLEMENTAL": len(records.supplemental_declarations),
        }
        report.seconds = time.perf_counter() - started
        print(report.render())
        return 0

    driver = _driver(args)
    try:
        loader = _loader(args, driver)
        loader.create_schema()
        print("writing …", file=sys.stderr)
        loader.load_records(records)
        if templates:
            loader.load_templates(templates)
        if eclass_result is not None:
            from neo4aas.vocabulary.concepts import (
                concepts_from_eclass,
                property_class_pairs,
            )

            # Streamed twice from the parse tree rather than materialised: a full
            # dictionary export is on the order of a million rows, and a dictionary
            # entry outranks a ConceptDescription copied into a vendor file anyway
            # (concepts.SOURCE_PRECEDENCE), so it is written after them.
            loader.load_concepts(concepts_from_eclass(eclass_result))
            loader.load_broader_prefix(concepts_from_eclass(eclass_result))
            if not args.no_property_class_edges:
                loader.load_broader_property_class(property_class_pairs(eclass_result))
        if cdd_result is not None:
            from neo4aas.vocabulary.iec_cdd import (
                class_broader_pairs,
                concepts_from_iec_cdd,
            )
            from neo4aas.vocabulary.iec_cdd import (
                property_class_pairs as cdd_property_class_pairs,
            )

            # CDD hierarchy is explicit, so there is no prefix pass: the export
            # names the superclass instead of encoding it in a CodedName.
            loader.load_concepts(concepts_from_iec_cdd(cdd_result))
            loader.load_broader_base_pairs(class_broader_pairs(cdd_result),
                                           name="BROADER(cdd class)")
            if not args.no_property_class_edges:
                loader.load_broader_base_pairs(cdd_property_class_pairs(cdd_result),
                                               name="BROADER(cdd property)")
        loader.finalize()
        report.seconds = time.perf_counter() - started
        collect(loader, report)
    finally:
        driver.close()

    print(report.render())
    if args.report_json:
        args.report_json.write_text(report.to_json())
        print(f"report written to {args.report_json}", file=sys.stderr)
    return 0


def command_stats(args) -> int:
    driver = _driver(args)
    try:
        report = collect(_loader(args, driver), RunReport())
    finally:
        driver.close()
    print(report.render())
    if args.report_json:
        args.report_json.write_text(report.to_json())
    return 0


def command_clear(args) -> int:
    driver = _driver(args)
    try:
        loader = _loader(args, driver)
        counts = loader.node_counts()
        total = sum(counts.values())
        if not args.yes:
            print(f"About to delete {total:,} vocabulary nodes from {args.uri}:")
            for label, count in sorted(counts.items()):
                print(f"  :{label:<12} {count:10,}")
            if input("Type 'delete' to confirm: ").strip() != "delete":
                print("aborted")
                return 1
        removed = loader.clear()
    finally:
        driver.close()
    for label, count in sorted(removed.items()):
        print(f"deleted {count:,} :{label}")
    return 0


COMMANDS = {"import": command_import, "stats": command_stats, "clear": command_clear}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    return COMMANDS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
