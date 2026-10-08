#!/usr/bin/env python3
"""Minimal, purpose-built extractor for e2e/scientists_queries.yaml.

Not a general YAML parser: relies on this file's specific, regular
structure (`- query: <name>`, `type: <type>`, `sparql: |` literal block),
since PyYAML isn't installed and this avoids adding a dependency just for
this one-off benchmark script.
"""
import json
import sys


def extract_queries(path):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    queries = []
    i = 0
    name = None
    qtype = None
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("- query:"):
            name = stripped[len("- query:"):].strip()
            qtype = None
        elif stripped.startswith("type:"):
            qtype = stripped[len("type:"):].strip()
        elif stripped.startswith("sparql:") and name is not None:
            # Collect the following indented literal block.
            block_indent = None
            body_lines = []
            i += 1
            while i < len(lines):
                l = lines[i]
                if l.strip() == "":
                    body_lines.append("")
                    i += 1
                    continue
                indent = len(l) - len(l.lstrip(" "))
                if block_indent is None:
                    block_indent = indent
                if indent < block_indent:
                    break
                body_lines.append(l[block_indent:].rstrip("\n"))
                i += 1
            sparql = "\n".join(body_lines).strip()
            queries.append({"name": name, "type": qtype, "sparql": sparql})
            name = None
            continue
        i += 1
    return queries


if __name__ == "__main__":
    queries = extract_queries(sys.argv[1])
    only_type = sys.argv[2] if len(sys.argv) > 2 else None
    if only_type:
        queries = [q for q in queries if q["type"] == only_type]
    print(json.dumps(queries, indent=2))
    print(f"# {len(queries)} queries", file=sys.stderr)
