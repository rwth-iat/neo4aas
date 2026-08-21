#!/usr/bin/env bash
# Acceptance evidence for the vocabulary graph (neo4aas vocab).
#
# Every claim the design makes is a command whose output is pasted into
# docs/vocabulary-graph.md. Nothing here asserts; it measures and prints.
#
#   ./scripts/bench/vocab_acceptance.sh idempotence   import a subset twice
#   ./scripts/bench/vocab_acceptance.sh tiers         1k / 10k / 50k node growth
#   ./scripts/bench/vocab_acceptance.sh full          whole corpus + dictionary
#   ./scripts/bench/vocab_acceptance.sh traversals    the six queries, timed
set -euo pipefail

URI="${URI:-bolt://localhost:7688}"
CORPUS="${CORPUS:-/Users/igorgarmaev/PycharmProjects/aas-corpus}"
INSTANCES="$CORPUS/instances/vendors"
TEMPLATES="$CORPUS/specs/submodel-templates"
ECLASS="${ECLASS:-$CORPUS/dictionaries/eclass/1730150000deDEbasicXML01}"
SEGMENTS="${SEGMENTS:-}"
WORKERS="${WORKERS:-8}"
OUT="${OUT:-docs/evidence}"
VOCAB=(uv run neo4aas vocab)

mkdir -p "$OUT"

banner() { printf '\n===== %s =====\n' "$1"; }

idempotence() {
    banner "1. IDEMPOTENCE — import 1 000 files twice"
    "${VOCAB[@]}" clear --uri "$URI" --yes >/dev/null
    for run in 1 2; do
        "${VOCAB[@]}" import --uri "$URI" --vendor-from-dir --stratify \
            --limit 1000 --workers "$WORKERS" "$INSTANCES" \
            > "$OUT/idempotence-run$run.txt" 2>/dev/null
        sed -n '/^NODES/,/^SEMANTIC ID RESOLUTION/p' "$OUT/idempotence-run$run.txt" \
            > "$OUT/idempotence-counts$run.txt"
    done
    cat "$OUT/idempotence-counts1.txt"
    echo "--- diff of run 1 against run 2 (empty means nothing moved) ---"
    diff "$OUT/idempotence-counts1.txt" "$OUT/idempotence-counts2.txt" && echo "(identical)"
}

tiers() {
    banner "2. SUBLINEARITY — node count against file count"
    printf '%8s %11s %8s %9s %10s %11s %8s\n' \
        files elements Shape "Shape/no#" CONTAINS SemanticId seconds
    for n in 1000 10000 50000; do
        "${VOCAB[@]}" clear --uri "$URI" --yes >/dev/null
        "${VOCAB[@]}" import --uri "$URI" --vendor-from-dir --stratify \
            --limit "$n" --workers "$WORKERS" "$INSTANCES" \
            > "$OUT/tier-$n.txt" 2>/dev/null
        # Section-aware: "Shape" and "CONTAINS" also name rows in the WRITES block,
        # and an unanchored match silently reports rows-written instead of the count.
        awk -v n="$n" '
            /^NODES/  { sec = "N" }
            /^EDGES/  { sec = "E" }
            /^WRITES/ { sec = "W" }
            /^wall clock/    { secs = $3 }
            /^elements seen/ { gsub(",", "", $3); el = $3 }
            sec == "N" && $1 == "Shape"      { gsub(",", "", $2); sh = $2 }
            sec == "N" && $1 == "SemanticId" { gsub(",", "", $2); se = $2 }
            sec == "E" && $1 == "CONTAINS"   { gsub(",", "", $2); ct = $2 }
            /shapes have an idShort with no/ { gsub(",", "", $1); plain = $1 }
            END { printf "%8d %11d %8d %9d %10d %11d %8.1f\n",
                         n, el, sh, plain, ct, se, secs }
        ' "$OUT/tier-$n.txt"
    done
}

full() {
    banner "3. FULL CORPUS"
    # Three loaders, three runs — each one is independently idempotent, and running
    # them separately keeps the ECLASS parse (a 2.4 GB XML tree) out of the same
    # process as the instance fold.
    "${VOCAB[@]}" clear --uri "$URI" --yes >/dev/null
    local seg=()
    [ -n "$SEGMENTS" ] && seg=(--eclass-segments "$SEGMENTS")

    echo "--- instances: $(find "$INSTANCES" -type f | wc -l | tr -d ' ') files ---"
    time "${VOCAB[@]}" import --uri "$URI" --vendor-from-dir --workers "$WORKERS" \
        --precedes --report-json "$OUT/full-instances.json" "$INSTANCES" \
        2>"$OUT/full-progress.txt" > "$OUT/full-instances.txt"
    tail -32 "$OUT/full-instances.txt"

    echo "--- templates ---"
    time "${VOCAB[@]}" import --uri "$URI" --templates "$TEMPLATES" \
        2>/dev/null > "$OUT/full-templates.txt"
    sed -n '/^NODES/,/^$/p' "$OUT/full-templates.txt"

    echo "--- dictionary ---"
    time "${VOCAB[@]}" import --uri "$URI" --eclass "$ECLASS" ${seg[@]+"${seg[@]}"} \
        --report-json "$OUT/full-report.json" 2>/dev/null > "$OUT/full-report.txt"
    tail -40 "$OUT/full-report.txt"
}

traversals() {
    # No `tee` into $OUT here: the caller redirects this function's stdout, and a
    # second writer on the same file interleaves the two streams mid-line.
    banner "4. THE SIX TRAVERSALS"
    uv run python scripts/bench/vocab_traversals.py --uri "$URI"
}

case "${1:-all}" in
    idempotence|tiers|full|traversals) "$1" ;;
    all) idempotence; tiers; full; traversals ;;
    *) echo "usage: $0 {idempotence|tiers|full|traversals|all}" >&2; exit 2 ;;
esac
