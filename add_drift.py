#!/usr/bin/env python3
"""
Adds `agentlens drift` - catch the catalog going stale.

Run from inside agentlens-project/ (after fix_lineage_cache.py):

    python add_drift.py
    ruff check --fix . && ruff check . && mypy agentlens && pytest -q

Why this exists
---------------
AgentLens's criticism of the hand-maintained approach is that a typed-out list
of dataset URNs is wrong the week after it's written. A scan is a snapshot, so
without this, the same criticism lands on AgentLens.

`drift` re-scans the repo, reads back what's actually in the catalog through
stored aspects (no lineage cache, same as impact.py), and reports the delta:

  * a skill whose instructions changed since it was catalogued
  * a skill that now reads a table it didn't read before
  * a skill that stopped reading one it used to
  * a reference that no longer resolves - the table was renamed or dropped
  * nodes in the repo that were never emitted, and nodes in the catalog whose
    source file is gone

It exits 1 when it finds drift and 0 when it doesn't, so it drops into CI
later without changes.
"""

import os
import sys

FILES = {}

# ===========================================================================
FILES["agentlens/drift.py"] = '''"""Has the catalog drifted from what the repos actually say?

A scan is a snapshot. The whole argument against hand-maintained agent
metadata is that it goes stale silently - which applies to a stale scan just
as well, so this closes that hole.

Everything here reads stored aspects rather than either GraphQL lineage field,
for the reasons set out at the top of impact.py: those two reads are backed by
two different caches and the one this project used to call has no `skipCache`.
A drift check that reports "no changes" off a stale cache would be worse than
no drift check at all.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .impact import PLATFORM_MARKER, _aspect, _upstreams_of, _urn_for, _urns_from_search
from .model import Manifest

# Ordered by how much someone should care.
SEVERITY = ["broken-ref", "ref-added", "ref-removed", "changed", "gone", "new"]

LABEL = {
    "broken-ref": "BROKEN ",
    "ref-added": "REF +  ",
    "ref-removed": "REF -  ",
    "changed": "CHANGED",
    "gone": "GONE   ",
    "new": "NEW    ",
}


@dataclass
class Change:
    kind: str
    node: str
    urn: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# what the repo says
# ---------------------------------------------------------------------------

def expected_state(manifest: Manifest) -> dict[str, dict[str, Any]]:
    """Mirror exactly what emit_manifest would write, without writing it."""
    tool_urn_by_name: dict[str, str] = {}
    state: dict[str, dict[str, Any]] = {}

    for tool in manifest.tools:
        urn = _urn_for("tool", f"{tool.server}.{tool.name}")
        tool_urn_by_name[tool.name] = urn
        state[urn] = {"id": tool.name, "kind": "tool", "sha": "", "upstreams": set()}

    skill_urn_by_id: dict[str, str] = {}
    for skill in manifest.skills:
        urn = _urn_for("skill", skill.id)
        skill_urn_by_id[skill.id] = urn
        upstreams = {r.resolved_urn for r in skill.data_refs if r.resolved_urn}
        upstreams |= {tool_urn_by_name[t] for t in skill.tools if t in tool_urn_by_name}
        state[urn] = {
            "id": skill.id,
            "kind": "skill",
            "sha": skill.instructions_sha,
            "upstreams": upstreams,
            "broken": [r.raw for r in skill.data_refs if not r.resolved_urn],
        }

    for agent in manifest.agents:
        urn = _urn_for("agent", agent.id)
        upstreams = {skill_urn_by_id[s] for s in agent.skills if s in skill_urn_by_id}
        upstreams |= {tool_urn_by_name[t] for t in agent.tools if t in tool_urn_by_name}
        state[urn] = {"id": agent.id, "kind": "agent", "sha": "", "upstreams": upstreams}

    return state


# ---------------------------------------------------------------------------
# what the catalog says
# ---------------------------------------------------------------------------

def read_catalog(urns: list[str]) -> dict[str, dict[str, Any] | None]:
    """None means the node is not in the catalog at all."""
    out: dict[str, dict[str, Any] | None] = {}
    for urn in urns:
        props = _aspect(urn, "datasetProperties")
        if props is None:
            out[urn] = None
            continue
        custom = props.get("customProperties") or {}
        out[urn] = {
            "sha": custom.get("agentlens.instructions_sha", ""),
            "upstreams": set(_upstreams_of(urn)),
        }
    return out


def catalogued_urns() -> list[str]:
    warnings: list[str] = []
    return [u for u in _urns_from_search(warnings) if PLATFORM_MARKER in u]


# ---------------------------------------------------------------------------
# the diff - pure, so it is testable without DataHub
# ---------------------------------------------------------------------------

def compare(
    expected: dict[str, dict[str, Any]],
    catalog: dict[str, dict[str, Any] | None],
    catalog_urns: list[str] | None = None,
) -> list[Change]:
    changes: list[Change] = []

    for urn, want in expected.items():
        node = want["id"]

        for raw in want.get("broken", []):
            changes.append(Change("broken-ref", node, urn, f"{raw} no longer resolves"))

        have = catalog.get(urn)
        if have is None:
            changes.append(Change("new", node, urn, "in the repo, not in the catalog"))
            continue

        if want["sha"] and have["sha"] and want["sha"] != have["sha"]:
            changes.append(Change(
                "changed", node, urn,
                f"instructions {have['sha'][:6]} -> {want['sha'][:6]}",
            ))

        for added in sorted(want["upstreams"] - have["upstreams"]):
            changes.append(Change("ref-added", node, urn, _short(added)))
        for removed in sorted(have["upstreams"] - want["upstreams"]):
            changes.append(Change("ref-removed", node, urn, _short(removed)))

    for urn in catalog_urns or []:
        if urn not in expected:
            changes.append(Change("gone", _short(urn), urn, "in the catalog, not in the repo"))

    return sorted(changes, key=lambda c: (SEVERITY.index(c.kind), c.node, c.detail))


def _short(urn: str) -> str:
    """`urn:li:dataset:(urn:li:dataPlatform:x,a.b.c,PROD)` -> `a.b.c`."""
    if "," not in urn:
        return urn
    parts = urn.split(",")
    return parts[1] if len(parts) > 1 else urn


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render(changes: list[Change], scanned: int) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("CATALOG DRIFT")
    lines.append("=" * 72)
    lines.append("")

    if not changes:
        lines.append(f"  No drift. {scanned} node(s) match what the repos say.")
        lines.append("")
        return chr(10).join(lines)

    lines.append(f"  {len(changes)} change(s) since the catalog was last written")
    lines.append("")

    width = max(len(c.node) for c in changes)
    for change in changes:
        lines.append(f"  {LABEL[change.kind]}  {change.node:<{width}}  {change.detail}")
    lines.append("")

    if any(c.kind == "broken-ref" for c in changes):
        lines.append("  A broken reference is a governance finding, not a bug in the scan:")
        lines.append("  the skill names a table the catalog does not have. Either the table")
        lines.append("  was renamed or dropped and the agent has been failing quietly, or")
        lines.append("  the table exists and was never catalogued.")
        lines.append("")

    lines.append("  Bring the catalog back in line with:")
    lines.append("    agentlens emit manifest.json")
    lines.append("")
    return chr(10).join(lines)
'''

# ===========================================================================
FILES["tests/test_drift.py"] = '''"""Drift detection. Pure diff, so no DataHub and no network."""

from agentlens.drift import Change, compare, expected_state, render
from agentlens.impact import _urn_for
from agentlens.model import Agent, DataRef, Manifest, Skill, Tool

ORDERS = "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.orders,PROD)"
SUBS = "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.subscriptions,PROD)"

SKILL_URN = _urn_for("skill", "revenue-lookup")


def _manifest(sha="aaa111", refs=((("analytics.orders"), ORDERS),)):
    return Manifest(
        agents=[Agent(id="finance-copilot", name="finance-copilot", skills=["revenue-lookup"])],
        skills=[Skill(
            id="revenue-lookup", name="revenue-lookup", instructions_sha=sha,
            data_refs=[DataRef(raw=raw, source_file="SKILL.md", resolved_urn=urn)
                       for raw, urn in refs],
        )],
        tools=[Tool(name="run_query", server="warehouse", source_file=".mcp.json")],
    )


def _catalog(expected, sha="aaa111", upstreams=(ORDERS,)):
    """A catalog that agrees with the manifest, unless a test perturbs it."""
    out = {}
    for urn, want in expected.items():
        out[urn] = {
            "sha": sha if want["kind"] == "skill" else "",
            "upstreams": set(upstreams) if want["kind"] == "skill" else set(want["upstreams"]),
        }
    return out


def test_no_drift_when_catalog_matches():
    expected = expected_state(_manifest())
    assert compare(expected, _catalog(expected)) == []


def test_detects_changed_instructions():
    expected = expected_state(_manifest(sha="bbb222"))
    changes = compare(expected, _catalog(expected, sha="aaa111"))
    assert [c.kind for c in changes] == ["changed"]
    assert "aaa111 -> bbb222" in changes[0].detail


def test_detects_a_newly_read_table():
    expected = expected_state(_manifest(
        refs=(("analytics.orders", ORDERS), ("analytics.subscriptions", SUBS)),
    ))
    changes = compare(expected, _catalog(expected, upstreams=(ORDERS,)))
    assert [c.kind for c in changes] == ["ref-added"]
    assert "analytics.subscriptions" in changes[0].detail


def test_detects_a_table_no_longer_read():
    expected = expected_state(_manifest())
    changes = compare(expected, _catalog(expected, upstreams=(ORDERS, SUBS)))
    assert [c.kind for c in changes] == ["ref-removed"]
    assert "analytics.subscriptions" in changes[0].detail


def test_unresolved_reference_is_reported_as_broken():
    expected = expected_state(_manifest(refs=(("analytics.events", None),)))
    changes = compare(expected, _catalog(expected, upstreams=()))
    kinds = [c.kind for c in changes]
    assert "broken-ref" in kinds
    assert any("analytics.events" in c.detail for c in changes)


def test_detects_a_node_that_was_never_emitted():
    expected = expected_state(_manifest())
    catalog = _catalog(expected)
    catalog[SKILL_URN] = None
    changes = compare(expected, catalog)
    assert [c.kind for c in changes] == ["new"]


def test_detects_a_node_whose_source_is_gone():
    expected = expected_state(_manifest())
    stale = _urn_for("skill", "deleted-skill")
    changes = compare(expected, _catalog(expected), catalog_urns=list(expected) + [stale])
    assert [c.kind for c in changes] == ["gone"]


def test_broken_refs_sort_above_everything_else():
    changes = [
        Change("new", "z", "urn:z", ""),
        Change("broken-ref", "a", "urn:a", "gone"),
        Change("changed", "m", "urn:m", ""),
    ]
    assert [c.kind for c in sorted(
        changes, key=lambda c: __import__("agentlens.drift", fromlist=["SEVERITY"]).SEVERITY.index(c.kind)
    )][0] == "broken-ref"


def test_render_is_explicit_when_clean():
    out = render([], scanned=9)
    assert "No drift" in out
    assert "9 node(s)" in out


def test_render_explains_broken_references():
    out = render([Change("broken-ref", "ownership-audit", "urn:x",
                         "analytics.events no longer resolves")], scanned=9)
    assert "governance finding" in out
    assert "ownership-audit" in out
'''


# ===========================================================================
CMD = '''

def cmd_drift(args) -> int:
    """Re-scan, compare against the catalog, report the delta."""
    manifest = scan(args.repo, args.repository or "")
    if not args.no_resolve:
        Resolver().resolve_manifest(manifest)

    expected = expected_state(manifest)
    catalog = read_catalog(list(expected))
    known = catalogued_urns() if not args.no_orphans else None
    changes = compare(expected, catalog, known)

    if args.format == "json":
        print(json.dumps({"changes": [c.to_dict() for c in changes],
                          "scanned": len(expected)}, indent=2))
    else:
        print(drift_render(changes, len(expected)))

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"changes": [c.to_dict() for c in changes],
                       "scanned": len(expected)}, fh, indent=2)

    return 1 if changes and not args.exit_zero else 0
'''

PARSER = '''    p = sub.add_parser("drift", help="has the catalog gone stale since the last emit?")
    p.add_argument("repo")
    p.add_argument("--repository", help="repo name to record, e.g. github.com/acme/agents")
    p.add_argument("--no-resolve", action="store_true", help="skip DataHub URN resolution")
    p.add_argument("--no-orphans", action="store_true",
                   help="skip the search for catalogued nodes whose source is gone")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--json", help="also write the changes as JSON")
    p.add_argument("--exit-zero", action="store_true",
                   help="always exit 0; by default drift exits 1 so CI can gate on it")
    p.set_defaults(func=cmd_drift)

'''


def patch_cli() -> bool:
    path = "agentlens/cli.py"
    if not os.path.exists(path):
        print("  MISS   cli.py not found")
        return False
    with open(path, encoding="utf-8") as fh:
        cli = fh.read()

    if "cmd_drift" in cli:
        print("  ok     cli: drift (already applied)")
        return True

    # Insert after .actions so the import block stays isort-clean.
    imp = "from .actions import Actions"
    if imp not in cli:
        print("  MISS   cli: actions import line")
        return False
    cli = cli.replace(
        imp,
        imp + "\n"
        "from .drift import (\n"
        "    catalogued_urns,\n"
        "    compare,\n"
        "    expected_state,\n"
        "    read_catalog,\n"
        ")\n"
        "from .drift import render as drift_render",
        1,
    )

    anchor = "\n\ndef main(argv=None) -> int:"
    if anchor not in cli:
        print("  MISS   cli: main()")
        return False
    cli = cli.replace(anchor, "\n" + CMD + anchor, 1)

    p_anchor = '    p = sub.add_parser("emit"'
    if p_anchor not in cli:
        print("  MISS   cli: emit subparser")
        return False
    cli = cli.replace(p_anchor, PARSER + p_anchor, 1)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(cli)
    print("  patch  cli: drift command")
    return True


def main() -> int:
    if not os.path.isdir("agentlens"):
        print("Run this from inside agentlens-project/.")
        return 1
    if not os.path.exists("agentlens/impact.py"):
        print("agentlens/impact.py missing.")
        return 1
    with open("agentlens/impact.py", encoding="utf-8") as fh:
        if "_urns_from_search" not in fh.read():
            print("Run fix_lineage_cache.py first - drift reuses its cache-free reads.")
            return 1

    for rel, content in FILES.items():
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        with open(rel, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"  write  {rel}")

    patch_cli()

    print("""
  Done.

  Try it on the demo repo - it should be clean right after an emit:

      python -m agentlens.cli drift demo-repo --repository github.com/acme/data-agents

  Then edit a SQL block in demo-repo/skills/revenue-lookup/SKILL.md so it reads
  a different table, and run it again. That is the demo: the catalog said one
  thing, the repo now says another, and nothing else in DataHub would notice.

  It exits 1 when it finds drift, so it drops into CI unchanged:

      agentlens drift . --exit-zero   # report only
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
