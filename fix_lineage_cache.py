#!/usr/bin/env python3
"""
Fixes the lineage-cache correctness bug in AgentLens.

Run from inside agentlens-project/:

    python fix_lineage_cache.py
    ruff check --fix . && ruff check . && mypy agentlens && pytest -q

Background
----------
DataHub exposes two GraphQL lineage reads, backed by two different caches:

  * `Dataset.lineage`     - `LineageInput` has no `skipCache`, and no
                            `searchFlags` member to borrow one from. No opt-out.
  * `searchAcrossLineage` - `SearchAcrossLineageInput.searchFlags.skipCache`
                            exists, but that is a different cache again.

Measured on GMS v1.5.0.6: after removing lineage, `searchAcrossLineage` was
still stale past 120s, while `Dataset.lineage` was stale at t+0 and clean from
t+30. Two caches, not one - and `impact.py` was calling the one with no opt-out.

`demo.sh` runs `emit` and then `guard` back to back. That is t+0, inside the
measured stale window. On a cold or stale cache the traversal returns nothing,
and the old `render()` printed:

    No agents downstream. Safe to change.

which is a confident, wrong all-clear - the exact failure mode this project
exists to prevent, and a direct violation of the rule in the agentlens-guard
skill ("Never report 'safe' from a failed or empty traversal").

What this changes
-----------------
1. `impact.py` gains a cache-free default read path. It enumerates the
   AgentLens nodes and reads each one's `upstreamLineage` **stored aspect**
   through the GMS aspect API, then inverts those edges locally. Stored aspects
   are not served from either lineage cache. `--lineage-source graphql`
   restores the old behaviour, explicitly.
2. The report carries `ok`, `source`, `warnings` and `nodes_examined`, and
   `render()` will not print a clean bill of health for a traversal it could
   not complete - nor for one that examined zero nodes.
3. The old hardcoded `count: 200` no longer truncates silently.
"""

import os
import sys

FILES = {}

# ===========================================================================
FILES["agentlens/impact.py"] = '''"""Blast radius: which agents and skills degrade if this asset changes?

This is the payoff. Everything else exists to make this query answerable - so
the read path has to be one we can trust.

DataHub exposes two GraphQL lineage reads, backed by two different caches:

  * ``Dataset.lineage``     - ``LineageInput`` has no ``skipCache`` and no
                              ``searchFlags`` member to borrow one from.
                              There is no opt-out.
  * ``searchAcrossLineage`` - ``SearchAcrossLineageInput.searchFlags.skipCache``
                              exists, but that is a different cache again.

Measured on GMS v1.5.0.6: after removing lineage, ``searchAcrossLineage`` was
still stale past 120s, while ``Dataset.lineage`` was stale at t+0 and clean
from t+30. Two caches, and this module used to call the one with no opt-out -
immediately after ``emit``, which is t+0, inside that window.

An empty traversal and a genuinely clear table look identical in the output.
Reporting the first as the second is the exact failure this tool exists to
prevent, so the default read path is neither lineage field: we enumerate the
AgentLens nodes, read each one's ``upstreamLineage`` **stored aspect** through
the GMS aspect API, and invert those edges locally. Stored aspects are not
served from either lineage cache.

``source="graphql"`` restores the old behaviour. It is never silent about it:
every report carries ``source``, ``ok``, ``warnings`` and ``nodes_examined``,
and :func:`render` refuses to print a clean bill of health for a traversal it
could not complete.

Scope note: this is entity-level (table-level) lineage. ``LineageInput`` takes
no column parameter and the stored ``upstreamLineage`` aspect read here is
entity-level too. Column-level lineage is a separate question.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

import requests

GMS = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080").rstrip("/")
TOKEN = os.environ.get("DATAHUB_GMS_TOKEN", "")

HEADERS = {"Content-Type": "application/json"}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"

PLATFORM = "agentlens"
PLATFORM_MARKER = f"dataPlatform:{PLATFORM}"

# The old code hardcoded 200 and never said when it hit it.
LINEAGE_PAGE = 1000

TIMEOUT = 30


# ---------------------------------------------------------------------------
# stored-aspect reads - the cache-free path
# ---------------------------------------------------------------------------

def _aspect(urn: str, name: str) -> dict[str, Any] | None:
    """Read a stored aspect straight from GMS.

    Returns the aspect body, or None if it does not exist. Raises on transport
    failure - a caller that cannot tell "absent" from "unreachable" is exactly
    how you end up reporting a false all-clear.
    """
    url = f"{GMS}/aspects/{quote(urn, safe='')}?aspect={name}&version=0"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    body = resp.json().get("aspect") or {}
    for value in body.values():          # {"com.linkedin.dataset.X": {...}}
        if isinstance(value, dict):
            return value
    return None


def _upstreams_of(urn: str) -> list[str]:
    aspect = _aspect(urn, "upstreamLineage")
    if not aspect:
        return []
    return [u["dataset"] for u in aspect.get("upstreams", []) if u.get("dataset")]


def _details_of(urn: str) -> dict[str, Any]:
    props = _aspect(urn, "datasetProperties") or {}
    subs = _aspect(urn, "subTypes") or {}
    custom = props.get("customProperties") or {}
    fallback = urn.split(",")[1] if "," in urn else urn
    return {
        "name": props.get("name") or fallback,
        "custom": custom,
        "subtypes": subs.get("typeNames") or [],
    }


# ---------------------------------------------------------------------------
# enumerating the AgentLens nodes
# ---------------------------------------------------------------------------

def _urn_for(kind: str, ident: str) -> str:
    safe = ident.replace(" ", "-").replace("/", "_")
    return f"urn:li:dataset:(urn:li:dataPlatform:{PLATFORM},{kind}.{safe},PROD)"


def _urns_from_manifest(path: str) -> list[str]:
    """Exact and entirely offline, when the manifest is to hand."""
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    urns = []
    for tool in data.get("tools", []):
        urns.append(_urn_for("tool", f"{tool.get('server', '')}.{tool.get('name', '')}"))
    for skill in data.get("skills", []):
        urns.append(_urn_for("skill", skill.get("id", "")))
    for agent in data.get("agents", []):
        urns.append(_urn_for("agent", agent.get("id", "")))
    return [u for u in urns if u]


SEARCH = """
query($q: String!, $count: Int!) {
  searchAcrossEntities(input: {types: [DATASET], query: $q, start: 0, count: $count}) {
    total
    searchResults { entity { urn } }
  }
}
"""


def _urns_from_search(warnings: list[str]) -> list[str]:
    """Fallback when there is no manifest.

    This reads the search index, not either lineage cache. The search index has
    its own eventual consistency, which is why the node count is reported and
    a zero result is never treated as a clean bill of health.
    """
    resp = requests.post(
        f"{GMS}/api/graphql",
        headers=HEADERS,
        json={"query": SEARCH, "variables": {"q": PLATFORM, "count": LINEAGE_PAGE}},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        warnings.append(f"node search failed: {body['errors'][0].get('message', '?')}")
        return []
    block = (body.get("data") or {}).get("searchAcrossEntities") or {}
    urns = [
        r["entity"]["urn"]
        for r in block.get("searchResults", [])
        if PLATFORM_MARKER in r["entity"]["urn"]
    ]
    total = block.get("total")
    if isinstance(total, int) and total > LINEAGE_PAGE:
        warnings.append(
            f"search returned {total} results but only the first {LINEAGE_PAGE} were read"
        )
    return urns


def build_downstream_index(
    manifest_path: str = "manifest.json",
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Invert every AgentLens node's stored upstreamLineage into a forward index.

    Returns (index, node_urns, warnings). The index maps an upstream URN to the
    AgentLens nodes that declare it as an upstream - i.e. downstream edges,
    reconstructed without touching a lineage cache.
    """
    warnings: list[str] = []
    nodes = _urns_from_manifest(manifest_path)
    if nodes:
        # A manifest lists what we *meant* to emit. Anything already in the
        # catalog from an earlier scan would be missed, so top up from search.
        found = set(nodes)
        for urn in _urns_from_search(warnings):
            if urn not in found:
                nodes.append(urn)
                found.add(urn)
    else:
        nodes = _urns_from_search(warnings)

    index: dict[str, list[str]] = {}
    for node in nodes:
        for upstream in _upstreams_of(node):
            index.setdefault(upstream, []).append(node)
    return index, nodes, warnings


# ---------------------------------------------------------------------------
# the old GraphQL path, kept behind an explicit opt-in
# ---------------------------------------------------------------------------

LINEAGE = """
query($urn: String!, $count: Int!) {
  entity(urn: $urn) {
    ... on Dataset {
      urn
      downstream: lineage(input: {direction: DOWNSTREAM, start: 0, count: $count}) {
        relationships { entity { urn } }
      }
    }
  }
}
"""


def _fetch_graphql(urn: str) -> list[str] | None:
    resp = requests.post(
        f"{GMS}/api/graphql",
        headers=HEADERS,
        json={"query": LINEAGE, "variables": {"urn": urn, "count": LINEAGE_PAGE}},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        return None
    entity = (body.get("data") or {}).get("entity")
    if not entity:
        return None
    rels = (entity.get("downstream") or {}).get("relationships") or []
    return [r["entity"]["urn"] for r in rels]


# ---------------------------------------------------------------------------
# blast radius
# ---------------------------------------------------------------------------

def blast_radius(
    root_urn: str,
    max_hops: int = 6,
    source: str = "aspects",
    manifest_path: str = "manifest.json",
) -> dict:
    """Walk downstream from an asset, collecting AgentLens nodes by hop distance.

    ``source="aspects"`` (default) reconstructs the graph from stored
    ``upstreamLineage`` aspects and never touches a lineage cache.
    ``source="graphql"`` uses ``Dataset.lineage``, which is cached with no
    opt-out; the report says so.
    """
    warnings: list[str] = []
    ok = True
    nodes: list[str] = []
    index: dict[str, list[str]] = {}

    if source == "aspects":
        try:
            index, nodes, warnings = build_downstream_index(manifest_path)
        except requests.RequestException as exc:
            return _degraded(root_urn, source, [f"could not reach GMS: {exc}"])
        if not nodes:
            warnings.append(
                "no AgentLens nodes found - nothing has been catalogued, or the "
                "emit has not landed yet"
            )
            ok = False
    else:
        warnings.append(
            "using Dataset.lineage, which is cached with no skipCache opt-out; "
            "results immediately after an emit may be stale"
        )

    seen: set[str] = {root_urn}
    frontier = [root_urn]
    found: list[dict] = []

    for hop in range(1, max_hops + 1):
        next_frontier: list[str] = []
        for urn in frontier:
            if source == "aspects":
                children = index.get(urn, [])
            else:
                try:
                    fetched = _fetch_graphql(urn)
                except requests.RequestException as exc:
                    warnings.append(f"lineage read failed for {urn}: {exc}")
                    ok = False
                    continue
                if fetched is None:
                    warnings.append(f"lineage read returned an error for {urn}")
                    ok = False
                    continue
                if len(fetched) >= LINEAGE_PAGE:
                    warnings.append(
                        f"{urn} hit the {LINEAGE_PAGE}-edge page limit; "
                        "downstream list is truncated"
                    )
                    ok = False
                children = fetched

            for child in children:
                if child in seen:
                    continue
                seen.add(child)
                next_frontier.append(child)
                if PLATFORM_MARKER not in child:
                    continue
                try:
                    detail = _details_of(child)
                except requests.RequestException as exc:
                    warnings.append(f"could not read properties of {child}: {exc}")
                    ok = False
                    continue
                custom = detail["custom"]
                subtypes = detail["subtypes"]
                found.append({
                    "urn": child,
                    "name": detail["name"],
                    "kind": custom.get("agentlens.kind", "unknown"),
                    "subtype": subtypes[0] if subtypes else "",
                    "repository": custom.get("agentlens.source_repository", ""),
                    "source_path": custom.get("agentlens.source_path", ""),
                    "owner_team": custom.get("agentlens.owner_team", ""),
                    "hops": hop,
                })
        frontier = next_frontier
        if not frontier:
            break

    return {
        "root": root_urn,
        "agents": [f for f in found if f["kind"] == "agent"],
        "skills": [f for f in found if f["kind"] == "skill"],
        "tools": [f for f in found if f["kind"] == "tool"],
        "total_downstream": len(seen) - 1,
        "total_downstream_scope": (
            "agentlens-subgraph" if source == "aspects" else "all-downstream"
        ),
        "source": source,
        "nodes_examined": len(nodes) if source == "aspects" else None,
        "ok": ok,
        "warnings": warnings,
    }


def _degraded(root_urn: str, source: str, warnings: list[str]) -> dict:
    return {
        "root": root_urn, "agents": [], "skills": [], "tools": [],
        "total_downstream": 0, "total_downstream_scope": "unknown",
        "source": source, "nodes_examined": 0, "ok": False, "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# rendering - the rule is: never claim safety from a traversal we did not finish
# ---------------------------------------------------------------------------

def render(report: dict) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("AGENT BLAST RADIUS")
    lines.append("=" * 72)
    lines.append("")
    lines.append("Changing:")
    lines.append(f"  {report['root']}")
    lines.append("")

    source = report.get("source", "aspects")
    examined = report.get("nodes_examined")
    detail = f"{examined} AgentLens node(s) examined" if examined is not None else ""
    lines.append(f"  read via: {source}{'  -  ' + detail if detail else ''}")

    for warning in report.get("warnings", []):
        lines.append(f"  ! {warning}")
    lines.append("")

    n_a = len(report["agents"])
    n_s = len(report["skills"])
    n_t = len(report["tools"])

    if not report.get("ok", True):
        lines.append("  TRAVERSAL INCOMPLETE - no result reported.")
        lines.append("")
        lines.append("  An empty traversal and a genuinely clear asset look the same")
        lines.append("  from here, so this is not a clean bill of health. Fix the")
        lines.append("  warnings above and run it again.")
        lines.append("")
        if n_a or n_s or n_t:
            lines.append(f"  (partial: {n_a} agent(s), {n_s} skill(s), {n_t} tool(s) seen so far)")
            lines.append("")
        return "\\n".join(lines)

    if not (n_a or n_s or n_t):
        lines.append("  No agents downstream.")
        if examined:
            lines.append(f"  All {examined} catalogued AgentLens node(s) were checked;")
            lines.append("  none read this asset. Safe to change.")
        else:
            lines.append("  Nothing was checked, so this is not a clean bill of health.")
        lines.append("")
        return "\\n".join(lines)

    lines.append(f"  {n_a} agent(s), {n_s} skill(s), {n_t} tool(s) degrade.")
    lines.append("")

    for label, items in (("AGENTS", report["agents"]),
                         ("SKILLS", report["skills"]),
                         ("TOOLS", report["tools"])):
        if not items:
            continue
        lines.append(f"  {label}")
        for item in sorted(items, key=lambda x: x["hops"]):
            owner = f"  [{item['owner_team']}]" if item["owner_team"] else ""
            hops = f"{item['hops']} hop" + ("" if item["hops"] == 1 else "s")
            lines.append(f"    - {item['name']}  ({hops}){owner}")
            if item["source_path"]:
                lines.append(f"        {item['repository']}/{item['source_path']}")
        lines.append("")

    return "\\n".join(lines)
'''

# ===========================================================================
FILES["tests/test_impact_safety.py"] = '''"""The one rule: never report safety from a traversal we could not finish.

No DataHub required - these drive render() with hand-built reports, which is
the whole point. The failure being guarded against is a *silent* one, so it
needs a test that fails loudly.
"""

from agentlens.impact import render

BASE = {
    "root": "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.orders,PROD)",
    "agents": [], "skills": [], "tools": [],
    "total_downstream": 0, "total_downstream_scope": "agentlens-subgraph",
    "source": "aspects",
}


def test_failed_traversal_never_says_safe():
    out = render({**BASE, "ok": False, "nodes_examined": 0,
                  "warnings": ["could not reach GMS"]})
    assert "Safe to change" not in out
    assert "TRAVERSAL INCOMPLETE" in out
    assert "could not reach GMS" in out


def test_zero_nodes_examined_never_says_safe():
    """The stale-cache case: the walk completed, but over an empty graph."""
    out = render({**BASE, "ok": True, "nodes_examined": 0, "warnings": []})
    assert "Safe to change" not in out
    assert "not a clean bill of health" in out


def test_genuinely_clear_asset_does_say_safe():
    out = render({**BASE, "ok": True, "nodes_examined": 12, "warnings": []})
    assert "Safe to change" in out
    assert "12 catalogued AgentLens node(s) were checked" in out


def test_affected_agents_are_named_with_their_team():
    out = render({
        **BASE, "ok": True, "nodes_examined": 12, "warnings": [],
        "agents": [{"urn": "u", "name": "finance-copilot", "kind": "agent",
                    "subtype": "AI Agent", "repository": "github.com/acme/data-agents",
                    "source_path": "agentlens.yaml", "owner_team": "fpa-platform",
                    "hops": 2}],
    })
    assert "finance-copilot" in out
    assert "fpa-platform" in out
    assert "Safe to change" not in out


def test_warnings_are_always_surfaced():
    out = render({**BASE, "ok": True, "nodes_examined": 3,
                  "warnings": ["using Dataset.lineage, which is cached"],
                  "source": "graphql"})
    assert "cached" in out
    assert "read via: graphql" in out
'''


# ===========================================================================
def patch_cli() -> bool:
    """Thread --lineage-source through, and stop cmd_guard claiming safety."""
    path = "agentlens/cli.py"
    if not os.path.exists(path):
        print("  MISS   cli.py not found")
        return False
    with open(path, encoding="utf-8") as fh:
        cli = fh.read()
    before = cli

    # 1. pass the new argument through to blast_radius
    cli = cli.replace(
        "blast_radius(args.urn, max_hops=args.hops)",
        'blast_radius(args.urn, max_hops=args.hops,\n'
        '                           source=getattr(args, "lineage_source", "aspects"))',
    )

    # 2. cmd_guard must not call a degraded traversal "no agents affected"
    old_guard = (
        '    n_agents = len(report["agents"])\n'
        "    if n_agents == 0 and not args.force:\n"
        '        print("[2/3] DECIDE - no agents affected, no action taken\\n")\n'
        "        return 0"
    )
    new_guard = (
        '    if not report.get("ok", True) and not args.force:\n'
        '        print("[2/3] DECIDE - traversal incomplete, refusing to report a result\\n")\n'
        '        print("        re-run once the warnings above are resolved, or pass --force\\n")\n'
        "        return 1\n"
        "\n"
        '    n_agents = len(report["agents"])\n'
        "    if n_agents == 0 and not args.force:\n"
        '        print("[2/3] DECIDE - no agents affected, no action taken\\n")\n'
        "        return 0"
    )
    if old_guard in cli:
        cli = cli.replace(old_guard, new_guard, 1)
    elif "traversal incomplete, refusing" in cli:
        pass
    else:
        print("  MISS   cli: cmd_guard early return (patch it by hand)")

    # 3. register the flag on both parsers that take a urn
    if '"--lineage-source"' not in cli:
        anchor = '    p.set_defaults(func=cmd_impact)'
        flag = (
            '    p.add_argument(\n'
            '        "--lineage-source",\n'
            '        choices=["aspects", "graphql"],\n'
            '        default="aspects",\n'
            '        help="aspects (default) reads stored upstreamLineage and is cache-free; "\n'
            '             "graphql uses Dataset.lineage, which is cached with no opt-out",\n'
            "    )\n"
        )
        if anchor in cli:
            cli = cli.replace(anchor, flag + anchor, 1)
        anchor_g = '    p.set_defaults(func=cmd_guard)'
        if anchor_g in cli:
            cli = cli.replace(anchor_g, flag + anchor_g, 1)

    if cli == before:
        print("  ok     cli: already patched")
        return True
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(cli)
    print("  patch  cli: --lineage-source, guard refuses degraded traversals")
    return True


def main() -> int:
    if not os.path.isdir("agentlens"):
        print("Run this from inside agentlens-project/.")
        return 1

    for rel, content in FILES.items():
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        with open(rel, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"  write  {rel}")

    patch_cli()

    print()
    print("""  Done.

  The bug: `emit` then `guard` back to back is t+0, inside the measured
  Dataset.lineage stale window. A stale read returned nothing, and nothing
  printed "Safe to change."

  Check it still says the right thing when the catalog is empty:

      python -m agentlens.cli impact "urn:li:dataset:(urn:li:dataPlatform:snowflake,nope,PROD)"

  and that the old path is now explicit about what it is:

      python -m agentlens.cli impact "<urn>" --lineage-source graphql

  Then re-run ./demo.sh - the guard step should report the same agents as
  before, but now with `read via: aspects` and a node count above it.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
