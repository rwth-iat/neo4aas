"""``neo4aas`` command line entry point.

One subcommand today — ``vocab``, the vocabulary-graph importer. It is kept
separate from the instance importer on purpose: the two share the neo4j driver
and nothing else.
"""

from __future__ import annotations

import sys

USAGE = """usage: neo4aas <command> [...]

commands:
  vocab   load, inspect or clear the vocabulary graph
          (neo4aas vocab import|stats|clear, see `neo4aas vocab --help`)
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if argv else 1
    command, rest = argv[0], argv[1:]
    if command == "vocab":
        from neo4aas.vocabulary.cli import main as vocab_main

        return vocab_main(rest)
    print(f"unknown command: {command}\n\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
