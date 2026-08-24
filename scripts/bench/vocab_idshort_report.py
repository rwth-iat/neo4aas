"""Print the full idShort evidence out of a ``vocab import --report-json`` file.

The console report prints the head of each list; the acceptance evidence is the
whole of it. Everything here reads the JSON — it never queries, so the numbers
cannot drift from the ones the import printed.
"""

from __future__ import annotations

import json
import sys


def main(path: str) -> int:
    report = json.load(open(path, encoding="utf-8"))
    forms = report.get("id_short_forms") or {}
    merged = report.get("id_short_merged") or []
    bridges = report.get("id_short_cross_vendor") or []
    stems = report.get("semantic_id_stems") or []

    if forms:
        print(f"raw idShorts       {forms['raw']:>7,}")
        print(f"normalised forms   {forms['norm']:>7,}   "
              f"{forms['raw'] / max(forms['norm'], 1):.3f}x collapse")
        print(f"bases              {forms['base']:>7,}   "
              f"{forms['raw'] / max(forms['base'], 1):.3f}x collapse")

    print(f"\n{len(merged):,} NORMALISED FORMS MERGE MORE THAN ONE RAW SPELLING")
    for row in merged:
        print(f"  {row['form'][:44]:<44} {' | '.join(row['spellings'])}")

    print(f"\n{len(bridges):,} FORMS SPAN MORE THAN ONE VENDOR")
    for row in bridges:
        print(f"  {row['form'][:44]:<44} {', '.join(row['vendors'])}")

    print(f"\n{len(stems):,} SEMANTIC IDS RANKED BY STEM RATIO")
    print(f"  {'ratio':>5} {'stems':>6} {'shapes':>6}  res  semanticId")
    for row in stems:
        print(f"  {row['ratio']:>5.2f} {row['stems']:>6,} {row['shapes']:>6,}"
              f"  {'yes' if row['resolves'] else ' no'}  {row['value']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
