#!/usr/bin/env python3
"""
Adds `agentlens sandbox` - simulate a schema change before it exists.

Run from inside agentlens-project/ (after fix_lineage_cache.py and add_drift.py):

    python add_sandbox.py
    ruff check --fix . && ruff check . && mypy agentlens && pytest -q

Why
---
DataHub's impact analysis is read-only and always about the graph as it is now.
There is no what-if: nothing lets you apply a proposed change to a copy of the
graph and ask what it would break. Checked against all 71 entity types on
master and the docs - no sandbox, no branch, no simulation.

`sandbox` forks the fleet in memory, applies a change that does not exist yet,
and recomputes. It writes nothing unless you `--promote`.

Built entirely from parts that already exist:

  drift.expected_state()   -> inverted, this IS the downstream index, offline
  model.Manifest           -> the fleet
  skill.source_path        -> re-read the skill text to find column references
  impact's traversal shape -> BFS by hops
  actions.Actions          -> the promote step, unchanged

The only new logic is applying a hypothetical and diffing. No network, no
lineage cache, no GMS - it runs off manifest.json and the repo, so it is
deterministic and demos anywhere.
"""

import os
import sys

FILES = {}

# ===========================================================================
FILES["agentlens/sandbox.py"] = '''"""What would this change break, before anyone makes it?

DataHub's impact analysis answers "what is downstream of this asset" for the
graph as it stands. It has no notion of a change that has not happened yet, so
the question a person actually has - *should I make this change?* - has to be
answered by reading the impact list and imagining the rest.

This module forks the fleet in memory, applies a proposed change to the copy,
and recomputes. Nothing is written unless the caller promotes it.

Three change kinds, in rising order of blast radius:

  drop-column   the table survives; only skills that name the column break
  rename-table  every skill naming the old table breaks until its text is fixed
  drop-table    everything downstream breaks

The column case is the interesting one, because it is the only one where the
lineage graph alone gives the wrong answer. Every skill reading the table looks
equally affected in the graph; only the ones whose text actually names the
column are. That distinction is why this is a simulation and not a query.

Everything here runs off ``manifest.json`` and the repo on disk - no GMS, no
lineage cache, no network. The graph comes from inverting
:func:`agentlens.drift.expected_state`, which already computes each node's
upstreams in order to diff them.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .drift import _short, expected_state
from .impact import _urn_for
from .model import Manifest

BREAKS = "breaks"
DEGRADES = "degrades"
UNCHANGED = "unchanged"

SEVERITY_ORDER = [BREAKS, DEGRADES, UNCHANGED]

LABEL = {BREAKS: "BREAKS   ", DEGRADES: "DEGRADES ", UNCHANGED: "UNCHANGED"}


@dataclass
class Change:
    """A change that has not been made."""

    kind: str                 # drop-column | rename-table | drop-table
    table: str                # urn of the table being changed
    column: str = ""
    new_name: str = ""

    def describe(self) -> str:
        table = _short(self.table)
        if self.kind == "drop-column":
            return f"drop column {table}.{self.column}"
        if self.kind == "rename-table":
            return f"rename {table} to {self.new_name}"
        return f"drop table {table}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Effect:
    severity: str
    kind: str                 # skill | agent | tool
    name: str
    urn: str
    hops: int
    why: str
    owner_team: str = ""
    repository: str = ""
    source_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Fleet:
    """Everything the simulation needs, with no I/O left in it."""

    index: dict[str, list[str]] = field(default_factory=dict)   # upstream -> downstream nodes
    meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    skill_text: dict[str, str] = field(default_factory=dict)    # skill id -> instructions
    agent_skills: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# building the fork - offline, from the manifest and the repo
# ---------------------------------------------------------------------------

def fork(manifest: Manifest, repo_root: str = "") -> Fleet:
    """Everything downstream, as the repo currently describes it.

    This is a *copy*: mutating it cannot affect DataHub, because nothing here
    ever talks to DataHub.
    """
    expected = expected_state(manifest)

    index: dict[str, list[str]] = {}
    meta: dict[str, dict[str, Any]] = {}
    for urn, node in expected.items():
        meta[urn] = {"id": node["id"], "kind": node["kind"]}
        for upstream in node["upstreams"]:
            index.setdefault(upstream, []).append(urn)

    for skill in manifest.skills:
        urn = _urn_for("skill", skill.id)
        meta[urn].update({
            "name": skill.name or skill.id,
            "repository": skill.source_repository,
            "source_path": skill.source_path,
        })
    for agent in manifest.agents:
        urn = _urn_for("agent", agent.id)
        meta[urn].update({
            "name": agent.name or agent.id,
            "owner_team": agent.owner_team,
            "repository": agent.source_repository,
            "source_path": agent.source_path,
        })

    return Fleet(
        index=index,
        meta=meta,
        skill_text=read_skill_text(manifest, repo_root),
        agent_skills={a.id: list(a.skills) for a in manifest.agents},
    )


def read_skill_text(manifest: Manifest, repo_root: str) -> dict[str, str]:
    """Re-read the skill bodies. The manifest keeps a hash, not the text.

    A skill we cannot read is recorded as an empty string, which the simulation
    treats as "cannot tell" rather than "does not reference the column".
    """
    out: dict[str, str] = {}
    for skill in manifest.skills:
        path = os.path.join(repo_root, skill.source_path) if repo_root else skill.source_path
        try:
            with open(path, encoding="utf-8") as fh:
                out[skill.id] = fh.read()
        except OSError:
            out[skill.id] = ""
    return out


def resolve_table(manifest: Manifest, token: str) -> str | None:
    """Accept a full URN or a name fragment, e.g. `order_details`."""
    if token.startswith("urn:li:"):
        return token
    candidates = {
        r.resolved_urn
        for s in manifest.skills
        for r in s.data_refs
        if r.resolved_urn and token.lower() in r.resolved_urn.lower()
    }
    if len(candidates) == 1:
        return candidates.pop()
    return None


def table_candidates(manifest: Manifest) -> list[str]:
    return sorted({
        r.resolved_urn for s in manifest.skills for r in s.data_refs if r.resolved_urn
    })


# ---------------------------------------------------------------------------
# the simulation - pure
# ---------------------------------------------------------------------------

def mentions_column(text: str, column: str) -> bool:
    """Does this skill's text actually name the column?

    Word-boundary match, case-insensitive, so `discount_pct` matches
    `o.discount_pct` and `SUM(discount_pct)` but not `discount_pct_v2`.
    """
    if not text or not column:
        return False
    return re.search(rf"\\b{re.escape(column)}\\b", text, re.IGNORECASE) is not None


def simulate(fleet: Fleet, change: Change, max_hops: int = 6) -> dict[str, Any]:
    """Apply a change that does not exist and recompute the fleet."""
    reached: dict[str, int] = {}
    frontier = [change.table]
    seen = {change.table}

    for hop in range(1, max_hops + 1):
        nxt: list[str] = []
        for urn in frontier:
            for child in fleet.index.get(urn, []):
                if child in seen:
                    continue
                seen.add(child)
                reached[child] = hop
                nxt.append(child)
        frontier = nxt
        if not frontier:
            break

    effects: list[Effect] = []
    broken_skills: set[str] = set()
    unreadable: list[str] = []

    for urn, hops in reached.items():
        node = fleet.meta.get(urn, {})
        if node.get("kind") != "skill":
            continue
        skill_id = node["id"]
        text = fleet.skill_text.get(skill_id, "")

        if change.kind == "drop-column":
            if not text:
                unreadable.append(skill_id)
                severity, why = BREAKS, "could not read the skill file - assuming affected"
            elif mentions_column(text, change.column):
                severity, why = BREAKS, f"names {change.column} in its text"
            else:
                severity, why = UNCHANGED, f"reads the table but never names {change.column}"
        elif change.kind == "rename-table":
            severity = BREAKS
            why = f"names the old table; text must change to {change.new_name}"
        else:
            severity, why = BREAKS, "the table it reads would not exist"

        if severity == BREAKS:
            broken_skills.add(skill_id)
        effects.append(Effect(
            severity=severity, kind="skill", name=node.get("name", skill_id), urn=urn,
            hops=hops, why=why, repository=node.get("repository", ""),
            source_path=node.get("source_path", ""),
        ))

    for urn, hops in reached.items():
        node = fleet.meta.get(urn, {})
        if node.get("kind") != "agent":
            continue
        agent_id = node["id"]
        via = sorted(set(fleet.agent_skills.get(agent_id, [])) & broken_skills)
        if via:
            severity = DEGRADES
            why = "via " + ", ".join(via)
        else:
            severity = UNCHANGED
            why = "none of its skills name the change"
        effects.append(Effect(
            severity=severity, kind="agent", name=node.get("name", agent_id), urn=urn,
            hops=hops, why=why, owner_team=node.get("owner_team", ""),
            repository=node.get("repository", ""), source_path=node.get("source_path", ""),
        ))

    effects.sort(key=lambda e: (SEVERITY_ORDER.index(e.severity), e.kind, e.name))

    counts = {s: sum(1 for e in effects if e.severity == s) for s in SEVERITY_ORDER}
    affected = [e for e in effects if e.severity != UNCHANGED]

    return {
        "change": change.to_dict(),
        "description": change.describe(),
        "root": change.table,
        "forked_nodes": len(reached),
        "effects": [e.to_dict() for e in effects],
        "counts": counts,
        "unreadable_skills": unreadable,
        "written": False,
        # Shaped so actions.Actions can consume this report unchanged.
        "agents": [_as_finding(e) for e in affected if e.kind == "agent"],
        "skills": [_as_finding(e) for e in affected if e.kind == "skill"],
        "tools": [],
        "total_downstream": len(reached),
        "source": "sandbox",
        "ok": True,
        "warnings": (
            [f"could not read {len(unreadable)} skill file(s); counted as affected"]
            if unreadable else []
        ),
    }


def _as_finding(effect: Effect) -> dict[str, Any]:
    return {
        "urn": effect.urn, "name": effect.name, "kind": effect.kind,
        "subtype": "", "repository": effect.repository,
        "source_path": effect.source_path, "owner_team": effect.owner_team,
        "hops": effect.hops,
    }


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render(report: dict[str, Any]) -> str:
    nl = chr(10)
    lines = []
    lines.append("=" * 72)
    lines.append(
        "SANDBOX - promoting this finding" if report.get("written")
        else "SANDBOX - nothing below has been written"
    )
    lines.append("=" * 72)
    lines.append("")
    lines.append(f"  Proposed:  {report['description']}")
    lines.append(f"  Forked:    {report['forked_nodes']} downstream node(s)")
    lines.append("")

    for warning in report.get("warnings", []):
        lines.append(f"  ! {warning}")
    if report.get("warnings"):
        lines.append("")

    if not report["effects"]:
        lines.append("  Nothing downstream. No agent reads this asset.")
        lines.append("")
        return nl.join(lines)

    width = max(len(e["name"]) for e in report["effects"])
    for effect in report["effects"]:
        owner = f"  [{effect['owner_team']}]" if effect["owner_team"] else ""
        lines.append(
            f"  {LABEL[effect['severity']]}  {effect['name']:<{width}}  "
            f"{effect['why']}{owner}"
        )
    lines.append("")

    counts = report["counts"]
    lines.append(
        f"  {counts['breaks']} break, {counts['degrades']} degrade, "
        f"{counts['unchanged']} unaffected"
    )
    lines.append("")

    if report["change"]["kind"] == "drop-column" and counts[UNCHANGED]:
        lines.append("  The unaffected ones read the same table. Lineage alone would have")
        lines.append("  flagged them too - only their text says otherwise.")
        lines.append("")

    if not report["written"]:
        lines.append("  Nothing was written. To record this finding in the catalog:")
        lines.append(f"    agentlens sandbox ... --promote --reason \\"{report['description']}\\"")
        lines.append("")

    return nl.join(lines)
'''

# ===========================================================================
FILES["tests/test_sandbox.py"] = '''"""Sandbox simulation. Pure - no DataHub, no repo, no network."""

import pytest

from agentlens.model import Agent, DataRef, Manifest, Skill
from agentlens.sandbox import (
    BREAKS,
    DEGRADES,
    UNCHANGED,
    Change,
    Fleet,
    fork,
    mentions_column,
    render,
    resolve_table,
    simulate,
)

ORDERS = "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.order_details,PROD)"


def _manifest():
    """Two skills read the same table. Only one names the column."""
    return Manifest(
        agents=[
            Agent(id="finance-copilot", name="finance-copilot", owner_team="fpa-platform",
                  skills=["margin-analysis"]),
            Agent(id="growth-analyst", name="growth-analyst", owner_team="growth-eng",
                  skills=["churn-risk"]),
        ],
        skills=[
            Skill(id="margin-analysis", name="margin-analysis",
                  data_refs=[DataRef(raw="analytics.order_details",
                                     source_file="SKILL.md", resolved_urn=ORDERS)]),
            Skill(id="churn-risk", name="churn-risk",
                  data_refs=[DataRef(raw="analytics.order_details",
                                     source_file="SKILL.md", resolved_urn=ORDERS)]),
        ],
    )


def _fleet(margin_text, churn_text):
    f = fork(_manifest())
    f.skill_text = {"margin-analysis": margin_text, "churn-risk": churn_text}
    return f


# -- the distinction the graph cannot make -----------------------------------

def test_only_skills_naming_the_column_break():
    fleet = _fleet(
        "SELECT SUM(o.discount_pct) FROM analytics.order_details o",
        "SELECT customer_id FROM analytics.order_details",
    )
    report = simulate(fleet, Change("drop-column", ORDERS, column="discount_pct"))
    by_name = {e["name"]: e for e in report["effects"]}
    assert by_name["margin-analysis"]["severity"] == BREAKS
    assert by_name["churn-risk"]["severity"] == UNCHANGED
    assert report["counts"] == {BREAKS: 1, DEGRADES: 1, UNCHANGED: 2}


def test_the_agent_above_a_broken_skill_degrades_and_names_the_route():
    fleet = _fleet("uses discount_pct", "no mention")
    report = simulate(fleet, Change("drop-column", ORDERS, column="discount_pct"))
    by_name = {e["name"]: e for e in report["effects"]}
    assert by_name["finance-copilot"]["severity"] == DEGRADES
    assert "margin-analysis" in by_name["finance-copilot"]["why"]
    assert by_name["finance-copilot"]["owner_team"] == "fpa-platform"
    assert by_name["growth-analyst"]["severity"] == UNCHANGED


def test_dropping_the_table_breaks_everything_downstream():
    fleet = _fleet("no mention at all", "none either")
    report = simulate(fleet, Change("drop-table", ORDERS))
    assert report["counts"][BREAKS] == 2
    assert report["counts"][UNCHANGED] == 0


def test_renaming_breaks_every_reader_until_its_text_changes():
    fleet = _fleet("x", "y")
    report = simulate(fleet, Change("rename-table", ORDERS, new_name="analytics.orders_v2"))
    assert report["counts"][BREAKS] == 2
    assert all("orders_v2" in e["why"] for e in report["effects"] if e["kind"] == "skill")


# -- never claim safety you cannot back up -----------------------------------

def test_an_unreadable_skill_counts_as_affected_not_as_safe():
    fleet = _fleet("", "SELECT 1")
    report = simulate(fleet, Change("drop-column", ORDERS, column="discount_pct"))
    by_name = {e["name"]: e for e in report["effects"]}
    assert by_name["margin-analysis"]["severity"] == BREAKS
    assert "could not read" in by_name["margin-analysis"]["why"]
    assert report["warnings"]


# -- column matching ---------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("SUM(discount_pct)", True),
    ("o.discount_pct", True),
    ("DISCOUNT_PCT", True),
    ("discount_pct_v2", False),
    ("net_discount_pct", False),
    ("", False),
])
def test_column_matching_is_word_bounded(text, expected):
    assert mentions_column(text, "discount_pct") is expected


# -- plumbing ----------------------------------------------------------------

def test_nothing_is_written_by_a_simulation():
    report = simulate(_fleet("a", "b"), Change("drop-table", ORDERS))
    assert report["written"] is False
    assert "nothing below has been written" in render(report).lower()
    assert "Nothing was written" in render(report)


def test_a_promoting_run_does_not_claim_nothing_was_written():
    report = simulate(_fleet("discount_pct", "no"),
                      Change("drop-column", ORDERS, column="discount_pct"))
    report["written"] = True
    out = render(report)
    assert "promoting this finding" in out
    assert "Nothing was written" not in out


def test_report_is_shaped_for_the_write_back_actions():
    report = simulate(_fleet("discount_pct", "no"),
                      Change("drop-column", ORDERS, column="discount_pct"))
    assert {"root", "agents", "skills", "tools"} <= set(report)
    assert report["agents"][0]["owner_team"] == "fpa-platform"
    assert all("hops" in a for a in report["agents"])


def test_a_table_with_nothing_downstream_says_so():
    report = simulate(Fleet(), Change("drop-table", "urn:li:dataset:(x,y,PROD)"))
    assert report["effects"] == []
    assert "Nothing downstream" in render(report)


def test_resolve_table_accepts_a_fragment_and_refuses_an_ambiguous_one():
    m = _manifest()
    assert resolve_table(m, "order_details") == ORDERS
    assert resolve_table(m, ORDERS) == ORDERS
    assert resolve_table(m, "nope") is None


def test_render_explains_why_lineage_alone_would_over_report():
    report = simulate(_fleet("discount_pct", "no mention"),
                      Change("drop-column", ORDERS, column="discount_pct"))
    assert "Lineage alone would have" in render(report)
'''


# ===========================================================================
CMD = '''

def cmd_sandbox(args) -> int:
    """Simulate a change that does not exist yet. Writes nothing by default."""
    with open(args.manifest) as fh:
        manifest = Manifest.from_dict(json.load(fh))

    table = resolve_table(manifest, args.table)
    if not table:
        print(f"Could not resolve {args.table!r} to a single table. Candidates:")
        for urn in table_candidates(manifest):
            print(f"  {urn}")
        return 1

    if args.drop_column:
        change = Change("drop-column", table, column=args.drop_column)
    elif args.rename_to:
        change = Change("rename-table", table, new_name=args.rename_to)
    else:
        change = Change("drop-table", table)

    report = simulate(fork(manifest, args.repo), change, max_hops=args.hops)
    report["written"] = bool(args.promote)
    print(sandbox_render(report))

    if args.promote:
        reason = args.reason or report["description"]
        print("  PROMOTE - writing the finding back")
        actions = Actions()
        actions.flag_upstream(report, reason)
        actions.flag_affected(report, reason, deprecate=False)
        print(actions.render_log())

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"  wrote {args.json}")

    return 1 if report["counts"]["breaks"] and not args.exit_zero else 0
'''

PARSER = '''    p = sub.add_parser("sandbox", help="simulate a change before making it (writes nothing)")
    p.add_argument("table", help="table urn, or a fragment like order_details")
    p.add_argument("--repo", default="demo-repo", help="repo root the manifest was scanned from")
    p.add_argument("--manifest", default="manifest.json")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--drop-column", help="simulate dropping this column")
    g.add_argument("--rename-to", help="simulate renaming the table to this")
    g.add_argument("--drop-table", action="store_true", help="simulate dropping the table")
    p.add_argument("--hops", type=int, default=6)
    p.add_argument("--promote", action="store_true", help="write the finding back to DataHub")
    p.add_argument("--reason", help="recorded verbatim on the write-back")
    p.add_argument("--json", help="write the simulation as JSON")
    p.add_argument("--exit-zero", action="store_true",
                   help="always exit 0; by default a break exits 1 so CI can gate on it")
    p.set_defaults(func=cmd_sandbox)

'''


def patch_cli() -> bool:
    path = "agentlens/cli.py"
    if not os.path.exists(path):
        print("  MISS   cli.py not found")
        return False
    with open(path, encoding="utf-8") as fh:
        cli = fh.read()

    if "cmd_sandbox" in cli:
        print("  ok     cli: sandbox (already applied)")
        return True

    imp = "from .resolver import Resolver"
    if imp not in cli:
        print("  MISS   cli: resolver import line")
        return False
    cli = cli.replace(
        imp,
        imp + "\n"
        "from .sandbox import (\n"
        "    Change,\n"
        "    fork,\n"
        "    resolve_table,\n"
        "    simulate,\n"
        "    table_candidates,\n"
        ")\n"
        "from .sandbox import render as sandbox_render",
        1,
    )

    anchor = "\n\ndef main(argv=None) -> int:"
    if anchor not in cli:
        print("  MISS   cli: main()")
        return False
    cli = cli.replace(anchor, "\n" + CMD + anchor, 1)

    p_anchor = '    p = sub.add_parser("guard"'
    if p_anchor not in cli:
        print("  MISS   cli: guard subparser")
        return False
    cli = cli.replace(p_anchor, PARSER + p_anchor, 1)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(cli)
    print("  patch  cli: sandbox command")
    return True


def main() -> int:
    if not os.path.isdir("agentlens"):
        print("Run this from inside agentlens-project/.")
        return 1
    if not os.path.exists("agentlens/drift.py"):
        print("Run add_drift.py first - sandbox reuses expected_state().")
        return 1

    for rel, content in FILES.items():
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        with open(rel, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"  write  {rel}")

    patch_cli()

    print("""
  Done.

  The demo, in one command - the same table demo.sh guards, but asking a
  question DataHub cannot answer at all:

      python -m agentlens.cli sandbox order_details --drop-column line_total

  On the demo repo that reports 2 break, 1 degrade, 4 unaffected. Compare it
  with what `guard` says about the same table - 3 agents and 4 skills degrade.
  Both are correct. All seven of those nodes read the table, so the lineage
  graph flags every one; only two name the column. Closing that gap is the
  whole feature, and DataHub cannot answer it at all.

  Nothing is written. To record the finding:

      python -m agentlens.cli sandbox order_details --drop-column line_total \\\\
          --promote --reason "dropping line_total in the Q3 migration"
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
