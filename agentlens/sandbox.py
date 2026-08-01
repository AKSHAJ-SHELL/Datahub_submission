"""What would this change break, before anyone makes it?

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
    return re.search(rf"\b{re.escape(column)}\b", text, re.IGNORECASE) is not None


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
        lines.append(f"    agentlens sandbox ... --promote --reason \"{report['description']}\"")
        lines.append("")

    return nl.join(lines)
