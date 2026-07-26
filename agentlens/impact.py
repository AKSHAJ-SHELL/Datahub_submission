"""Blast radius: which agents and skills degrade if this asset changes?

This is the payoff. Everything else exists to make this query answerable.
"""

from __future__ import annotations

import os

import requests

GMS = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080").rstrip("/")
TOKEN = os.environ.get("DATAHUB_GMS_TOKEN", "")

HEADERS = {"Content-Type": "application/json"}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"

LINEAGE = """
query($urn: String!) {
  entity(urn: $urn) {
    ... on Dataset {
      urn
      properties { name customProperties { key value } }
      subTypes { typeNames }
      downstream: lineage(input: {direction: DOWNSTREAM, start: 0, count: 200}) {
        relationships { entity { urn } }
      }
    }
  }
}
"""


def _fetch(urn: str) -> dict | None:
    resp = requests.post(
        f"{GMS}/api/graphql",
        headers=HEADERS,
        json={"query": LINEAGE, "variables": {"urn": urn}},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        return None
    return body.get("data", {}).get("entity")


def blast_radius(root_urn: str, max_hops: int = 6) -> dict:
    """Walk downstream from an asset, collecting AgentLens nodes by hop distance."""
    seen: set[str] = {root_urn}
    frontier = [root_urn]
    found: list[dict] = []

    for hop in range(1, max_hops + 1):
        next_frontier: list[str] = []
        for urn in frontier:
            entity = _fetch(urn)
            if not entity:
                continue
            for rel in entity.get("downstream", {}).get("relationships", []):
                child = rel["entity"]["urn"]
                if child in seen:
                    continue
                seen.add(child)
                next_frontier.append(child)

                if "dataPlatform:agentlens" in child:
                    detail = _fetch(child) or {}
                    props = detail.get("properties") or {}
                    custom = {
                        c["key"]: c["value"]
                        for c in (props.get("customProperties") or [])
                    }
                    subtypes = (detail.get("subTypes") or {}).get("typeNames") or []
                    found.append({
                        "urn": child,
                        "name": props.get("name", child.split(",")[1] if "," in child else child),
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

    agents = [f for f in found if f["kind"] == "agent"]
    skills = [f for f in found if f["kind"] == "skill"]
    tools = [f for f in found if f["kind"] == "tool"]

    return {
        "root": root_urn,
        "agents": agents,
        "skills": skills,
        "tools": tools,
        "total_downstream": len(seen) - 1,
    }


def render(report: dict) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("AGENT BLAST RADIUS")
    lines.append("=" * 72)
    lines.append(f"\nChanging:\n  {report['root']}\n")

    n_a, n_s, n_t = len(report["agents"]), len(report["skills"]), len(report["tools"])
    if not (n_a or n_s or n_t):
        lines.append("  No agents downstream. Safe to change.\n")
        return "\n".join(lines)

    lines.append(f"  {n_a} agent(s), {n_s} skill(s), {n_t} tool(s) degrade.\n")

    for label, items in (("AGENTS", report["agents"]),
                         ("SKILLS", report["skills"]),
                         ("TOOLS", report["tools"])):
        if not items:
            continue
        lines.append(f"  {label}")
        for item in sorted(items, key=lambda x: x["hops"]):
            owner = f"  [{item['owner_team']}]" if item["owner_team"] else ""
            lines.append(f"    - {item['name']}  ({item['hops']} hops){owner}")
            if item["source_path"]:
                lines.append(f"        {item['repository']}/{item['source_path']}")
        lines.append("")

    return "\n".join(lines)
