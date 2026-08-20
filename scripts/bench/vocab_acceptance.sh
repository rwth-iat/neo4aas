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
    printf '%8s %11s %8s %8s %10s %11s %8s\n' \
        files elements Shape "Shape/no#" Structure SemanticId seconds
    for n in 1000 10000 50000; do
        "${VOCAB[@]}" clear --uri "$URI" --yes >/dev/null
        "${VOCAB[@]}" import --uri "$URI" --vendor-from-dir --stratify \
            --limit "$n" --workers "$WORKERS" "$INSTANCES" \
            > "$OUT/tier-$n.txt" 2>/dev/null
        awk -v n="$n" '
            /^wall clock/    { secs=$3 }
            /^elements seen/ { gsub(",","",$3); el=$3 }
            /^  Shape /      { gsub(",","",$2); sh=$2 }
            /^  Structure /  { gsub(",","",$2); st=$2 }
            /^  SemanticId / { gsub(",","",$2); se=$2 }
            /shapes have an idShort with no/ { gsub(",","",$1); plain=$1 }
            END { printf "%8d %11d %8d %8d %10d %11d %8.1f\n",
                         n, el, sh, plain, st, se, secs }
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
        --report-json "$OUT/full-instances.json" "$INSTANCES" \
        2>"$OUT/full-progress.txt" | tee "$OUT/full-instances.txt" | tail -32

    echo "--- templates ---"
    time "${VOCAB[@]}" import --uri "$URI" --templates "$TEMPLATES" \
        2>/dev/null | tee "$OUT/full-templates.txt" | sed -n '/^NODES/,/^$/p'

    echo "--- dictionary ---"
    time "${VOCAB[@]}" import --uri "$URI" --eclass "$ECLASS" ${seg[@]+"${seg[@]}"} \
        --report-json "$OUT/full-report.json" 2>/dev/null \
        | tee "$OUT/full-report.txt" | tail -40
}

traversals() {
    banner "4. THE SIX TRAVERSALS"
    uv run python scripts/bench/vocab_traversals.py --uri "$URI" \
        | tee "$OUT/traversals.txt"
}

case "${1:-all}" in
    idempotence|tiers|full|traversals) "$1" ;;
    all) idempotence; tiers; full; traversals ;;
    *) echo "usage: $0 {idempotence|tiers|full|traversals|all}" >&2; exit 2 ;;
esac
