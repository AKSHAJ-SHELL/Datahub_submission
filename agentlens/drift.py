"""Has the catalog drifted from what the repos actually say?

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
