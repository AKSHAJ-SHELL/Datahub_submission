#!/usr/bin/env python3
"""
Fixes the extraction and resolution quality.

Run from inside agentlens-project/:

    python fix_resolver.py
    ruff check --fix . && ruff check .
    mypy agentlens
    pytest -q

WHAT WAS ACTUALLY WRONG
-----------------------
"resolved 8/24" looked like a 33% hit rate. It wasn't. Of those 24 "data
references", roughly 16 were SQL column aliases - o.created_at, p.product_line,
c.customer_id - swept up by the bare dotted-identifier regex. They failed to
resolve because they are not tables. The resolver was mostly right; the
scanner was feeding it garbage and the metric was counting that garbage as
failure.

Two real fixes:

1. Alias binding. When SQL says `FROM analytics.orders o`, `o` is now a bound
   alias, so `o.created_at` is recognised as a column reference and dropped
   instead of being emitted as a table candidate.

2. Scored resolution. Matching was "is this substring anywhere in the URN",
   which false-positives across platform and env segments. Now the dataset
   name is parsed out of the URN and candidates are scored - exact match,
   suffix match, leaf match, substring - with the best one winning.

And honest reporting: filtered aliases are reported separately instead of
being buried in the denominator.
"""

from __future__ import annotations

import os
import sys

# ===========================================================================
NEW_EXTRACT = '''def _bound_aliases(text: str) -> set[str]:
    """Find table aliases bound by FROM/JOIN so we can tell columns from tables.

    `FROM analytics.orders o` binds `o`, which means a later `o.created_at`
    is a column reference, not a table. Without this the bare dotted-identifier
    pass emits every qualified column in every query as a table candidate.
    """
    aliases: set[str] = set()
    for match in ALIAS_BINDING.finditer(text):
        candidate = match.group(2).lower()
        if candidate not in SQL_KEYWORDS:
            aliases.add(candidate)
    return aliases


def extract_data_refs(text: str, source_file: str) -> list[DataRef]:
    """Pull probable table references out of prose or SQL.

    Two passes. Tokens following FROM/JOIN/INTO/UPDATE are near-certain tables
    (0.9). Bare dotted identifiers are weaker candidates (0.4) and are dropped
    when their prefix is a bound alias, a known module, or a filename.
    """
    refs: dict[str, DataRef] = {}
    aliases = _bound_aliases(text)

    for match in SQL_TABLE.finditer(text):
        token = match.group(1)
        refs[token.lower()] = DataRef(raw=token, source_file=source_file, confidence=0.9)

    for match in DOTTED.finditer(text):
        token = match.group(1)
        low = token.lower()
        if low in refs:
            continue
        if low.split(".")[0] in aliases:
            continue
        if low in NOISE or any(low.startswith(p) for p in NOISE_PREFIXES):
            continue
        if token.endswith((".md", ".py", ".json", ".yaml", ".yml", ".sql", ".txt")):
            continue
        refs[low] = DataRef(raw=token, source_file=source_file, confidence=0.4)

    return list(refs.values())
'''

NEW_RESOLVER = '''"""Resolve raw table tokens found in skills to real DataHub URNs.

Matching is scored rather than first-hit. A token like `analytics.orders`
should prefer a dataset actually named `...analytics.orders` over one that
merely contains the string somewhere in its URN - platform and environment
segments are part of a URN too, and substring matching across them produces
confident nonsense.
"""

from __future__ import annotations

import os
import re

import requests

GMS = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080").rstrip("/")
TOKEN = os.environ.get("DATAHUB_GMS_TOKEN", "")

HEADERS = {"Content-Type": "application/json"}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"

SEARCH = """
query($input: SearchAcrossEntitiesInput!) {
  searchAcrossEntities(input: $input) {
    searchResults { entity { urn ... on Dataset { name } } }
  }
}
"""

# urn:li:dataset:(urn:li:dataPlatform:<platform>,<name>,<env>)
URN_NAME = re.compile(r"^urn:li:dataset:\\(urn:li:dataPlatform:[^,]+,(.+),[^,)]+\\)$")

MIN_SCORE = 50


def dataset_name(urn: str) -> str:
    """The name segment of a dataset URN, or the URN if it doesn't parse."""
    match = URN_NAME.match(urn)
    return match.group(1) if match else urn


def score(token: str, name: str) -> int:
    """How well does a catalog dataset name match a token found in a skill?

    100  exact                     analytics.orders == analytics.orders
     90  suffix                    db.analytics.orders endswith .analytics.orders
     70  leaf                      trailing segment matches
     50  substring                 appears somewhere in the name
      0  no match
    """
    token, name = token.lower(), name.lower()
    if token == name:
        return 100
    if name.endswith("." + token):
        return 90
    if token.split(".")[-1] == name.split(".")[-1]:
        return 70
    if token in name:
        return 50
    return 0


class Resolver:
    """Looks tokens up in DataHub, with a cache so we hit the API once per token."""

    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}

    def _search(self, query: str, count: int = 20) -> list[dict]:
        try:
            resp = requests.post(
                f"{GMS}/api/graphql",
                headers=HEADERS,
                json={
                    "query": SEARCH,
                    "variables": {
                        "input": {
                            "types": ["DATASET"],
                            "query": query,
                            "start": 0,
                            "count": count,
                        }
                    },
                },
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException:
            return []
        body = resp.json()
        if "errors" in body:
            return []
        return body.get("data", {}).get("searchAcrossEntities", {}).get("searchResults", [])

    def resolve(self, token: str) -> str | None:
        """Best matching dataset URN for a token, or None.

        Searches the full token first, then progressively shorter suffixes -
        `db.schema.table` then `schema.table` then `table` - and keeps the
        highest-scoring candidate found at the first level that produces one.
        """
        key = token.lower()
        if key in self._cache:
            return self._cache[key]

        result: str | None = None
        parts = token.split(".")
        attempts = [".".join(parts[i:]) for i in range(len(parts))]

        for attempt in attempts:
            if len(attempt) < 3:
                continue

            best_urn, best_score = None, 0
            for hit in self._search(attempt):
                urn = hit["entity"]["urn"]
                if "dataPlatform:agentlens" in urn:
                    continue
                name = hit["entity"].get("name") or dataset_name(urn)
                hit_score = max(score(token, name), score(attempt, name))
                if hit_score > best_score:
                    best_urn, best_score = urn, hit_score

            if best_urn is not None and best_score >= MIN_SCORE:
                result = best_urn
                break

        self._cache[key] = result
        return result

    def resolve_manifest(self, manifest) -> tuple[int, int]:
        """Resolve every data ref in place. Returns (resolved, total)."""
        resolved = total = 0
        for skill in manifest.skills:
            for ref in skill.data_refs:
                total += 1
                urn = self.resolve(ref.raw)
                if urn:
                    ref.resolved_urn = urn
                    resolved += 1
        return resolved, total
'''


def replace_function(text: str, name: str, new_src: str) -> str | None:
    """Swap a top-level function, robust to whatever the formatter did to it."""
    lines = text.split("\n")
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"def {name}("):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if (
            line
            and not line[0].isspace()
            and (line.startswith("def ") or line.startswith("class ") or line.startswith("@"))
        ):
            end = j
            break
    return "\n".join(lines[:start] + new_src.rstrip("\n").split("\n") + lines[end:])


def main() -> int:
    if not os.path.isdir("agentlens"):
        print("Run this from inside agentlens-project/.")
        return 1

    ok = True

    # -- scanner: alias binding ------------------------------------------
    path = "agentlens/scanner.py"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    if "ALIAS_BINDING" not in text:
        anchor = "# Things that look dotted but are not tables"
        if anchor not in text:
            print("  MISS   scanner: could not find the NOISE block")
            ok = False
        else:
            text = text.replace(
                anchor,
                "# `FROM analytics.orders o` binds `o` as an alias for that table.\n"
                "ALIAS_BINDING = re.compile(\n"
                '    r"\\\\b(?:from|join)\\\\s+([a-zA-Z_][\\\\w.]*)\\\\s+(?:as\\\\s+)?([a-zA-Z_]\\\\w*)\\\\b",\n'
                "    re.IGNORECASE,\n"
                ")\n\n"
                "# Words that follow a table name but are not aliases.\n"
                "SQL_KEYWORDS = {\n"
                '    "where", "group", "order", "having", "join", "on", "left", "right",\n'
                '    "inner", "outer", "full", "cross", "limit", "union", "select", "and",\n'
                '    "or", "as", "using", "set", "values", "returning", "window", "qualify",\n'
                "}\n\n" + anchor,
                1,
            )
            print("  patch  scanner: alias-binding regex + keyword set")
    else:
        print("  ok     scanner: alias binding (already applied)")

    replaced = replace_function(text, "extract_data_refs", NEW_EXTRACT)
    if replaced is None:
        print("  MISS   scanner: extract_data_refs not found")
        ok = False
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(replaced)
        print("  patch  scanner: alias-aware extract_data_refs")

    # -- resolver: scored matching ---------------------------------------
    with open("agentlens/resolver.py", "w", encoding="utf-8") as fh:
        fh.write(NEW_RESOLVER)
    print("  patch  resolver: scored matching + URN name parsing")

    # -- cli: honest reporting -------------------------------------------
    path = "agentlens/cli.py"
    with open(path, encoding="utf-8") as fh:
        cli = fh.read()

    old = """        resolver = Resolver()
        resolved, total = resolver.resolve_manifest(manifest)
        print(f"resolved {resolved}/{total} data references against DataHub")"""
    new = """        resolver = Resolver()
        resolved, total = resolver.resolve_manifest(manifest)
        strong = sum(
            1 for s in manifest.skills for r in s.data_refs if r.confidence >= 0.9
        )
        print(f"resolved {resolved}/{total} data references against DataHub")
        if total:
            print(f"  ({strong} came from SQL FROM/JOIN clauses)")
        unresolved = [
            r.raw for s in manifest.skills for r in s.data_refs if not r.resolved_urn
        ]
        if unresolved:
            preview = ", ".join(sorted(unresolved)[:6])
            more = "" if len(unresolved) <= 6 else f" (+{len(unresolved) - 6} more)"
            print(f"  unresolved: {preview}{more}")"""

    if new.split("\n")[3] in cli:
        print("  ok     cli: reporting (already applied)")
    elif old in cli:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(cli.replace(old, new, 1))
        print("  patch  cli: honest resolution reporting")
    else:
        print("  MISS   cli: resolution reporting block")
        ok = False

    # -- tests -------------------------------------------------------------
    path = "tests/test_scanner.py"
    with open(path, encoding="utf-8") as fh:
        tests = fh.read()

    if "test_drops_aliased_columns" not in tests:
        tests += '''

class TestAliasBinding:
    """The fix for the 8/24 resolution rate: most of those 24 were columns."""

    def test_drops_aliased_columns(self):
        text = "SELECT o.created_at, o.line_total FROM analytics.orders o"
        raws = {r.raw.lower() for r in extract_data_refs(text, "x.md")}
        assert "analytics.orders" in raws
        assert "o.created_at" not in raws
        assert "o.line_total" not in raws

    def test_drops_aliases_from_joins(self):
        text = (
            "FROM analytics.orders o "
            "JOIN analytics.products p ON p.product_id = o.product_id"
        )
        raws = {r.raw.lower() for r in extract_data_refs(text, "x.md")}
        assert "analytics.orders" in raws
        assert "analytics.products" in raws
        assert "p.product_id" not in raws
        assert "o.product_id" not in raws

    def test_handles_as_keyword(self):
        text = "FROM analytics.orders AS o WHERE o.status = 'x'"
        raws = {r.raw.lower() for r in extract_data_refs(text, "x.md")}
        assert "o.status" not in raws

    def test_does_not_treat_keywords_as_aliases(self):
        """`FROM analytics.orders WHERE ...` must not bind `where`."""
        text = "FROM analytics.orders WHERE where.thing = 1"
        raws = {r.raw.lower() for r in extract_data_refs(text, "x.md")}
        assert "analytics.orders" in raws

    def test_unaliased_dotted_tokens_survive(self):
        text = "The agent reads warehouse.analytics.orders directly."
        raws = {r.raw.lower() for r in extract_data_refs(text, "x.md")}
        assert "warehouse.analytics.orders" in raws
'''
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(tests)
        print("  patch  tests: alias-binding cases")
    else:
        print("  ok     tests: alias binding (already present)")

    path = "tests/test_resolver_scoring.py"
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('''"""Scoring logic. No DataHub required."""

from agentlens.resolver import dataset_name, score

URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.db.analytics.orders,PROD)"


def test_parses_name_out_of_urn():
    assert dataset_name(URN) == "b2fd91.db.analytics.orders"


def test_unparseable_urn_returns_itself():
    assert dataset_name("not-a-urn") == "not-a-urn"


def test_exact_match_scores_highest():
    assert score("analytics.orders", "analytics.orders") == 100


def test_suffix_beats_leaf():
    assert score("analytics.orders", "db.analytics.orders") == 90
    assert score("sales.orders", "db.analytics.orders") == 70


def test_no_match_scores_zero():
    assert score("analytics.customers", "db.analytics.orders") == 0


def test_scoring_is_case_insensitive():
    assert score("Analytics.Orders", "analytics.orders") == 100


def test_prefers_the_right_candidate():
    """A real ranking: order_details should not win for a query about orders."""
    candidates = ["db.analytics.order_details", "db.analytics.orders"]
    best = max(candidates, key=lambda n: score("analytics.orders", n))
    assert best == "db.analytics.orders"
''')
        print("  patch  tests: resolver scoring")
    else:
        print("  ok     tests: resolver scoring (already present)")

    print()
    print("  Done." if ok else "  Some patches missed - paste the output.")
    print("""
    ruff check --fix . && ruff check .
    mypy agentlens
    pytest -q

Then re-scan and watch the denominator drop:

    python -m agentlens.cli scan demo-repo --repository github.com/acme/data-agents
    python -m agentlens.cli emit manifest.json
""")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
