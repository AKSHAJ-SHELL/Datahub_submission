#!/usr/bin/env python3
"""
Packages AgentLens as agent skills.

Run from inside agentlens-project/:

    python add_skills.py
    ruff check --fix . && ruff check . && mypy agentlens && pytest -q

Adds:
  * `--format json` on `impact`, so an agent can parse the result instead of
    scraping terminal output
  * skills/agentlens-catalog/SKILL.md  - catalogue an agent fleet
  * skills/agentlens-guard/SKILL.md    - check blast radius before a change
  * .mcp.json at the repo root, so the repo describes its own agent surface
    (and so AgentLens can scan itself)
"""

import os
import sys

FILES = {}

# ===========================================================================
FILES["skills/agentlens-guard/SKILL.md"] = '''---
name: agentlens-guard
description: >
  Before any schema change, deprecation, or table rename, work out which AI
  agents lose context. Use when someone proposes dropping or renaming a
  column, deprecating a table, changing a data type, or asks "is it safe to
  change this?". Reads DataHub lineage, reports affected agents by name and
  owning team, and writes the finding back to the catalog once approved.
allowed-tools:
  - Bash
  - mcp__datahub__search
  - mcp__datahub__get_lineage
  - mcp__datahub__get_entities
---

# agentlens-guard

## Why this exists

When a dashboard's upstream column disappears, the dashboard errors and
someone gets paged. When an agent's upstream column disappears, the agent does
not error. It answers confidently and wrongly, and nobody finds out until a
decision has been made on the answer.

Impact analysis in DataHub covers dashboards, pipelines and models. It does not
cover agents, because agents are not in the graph. AgentLens puts them there.
This skill checks them before a change ships.

## Workflow

### 1. Resolve the asset

The user will name a table informally — "the orders table", "order_details".
Resolve it to a URN with the DataHub `search` tool. If more than one candidate
matches, show them and ask which. **Never guess between two plausible tables** —
guarding the wrong one produces a confident all-clear, which is the exact
failure this skill exists to prevent.

### 2. Read the blast radius

```bash
python -m agentlens.cli impact "<urn>" --format json
```

Returns:

```json
{
  "root": "urn:li:dataset:(...)",
  "agents": [
    {"name": "finance-copilot", "kind": "agent", "hops": 2,
     "owner_team": "fpa-platform", "repository": "github.com/acme/data-agents",
     "source_path": "agentlens.yaml"}
  ],
  "skills": [...],
  "tools": [...],
  "total_downstream": 38
}
```

If the command fails or `total_downstream` is 0 on a table you'd expect to have
consumers, **say so and stop**. Do not report "no agents affected" — an empty
traversal and a genuinely clear table look identical in the output, and
reporting the first as the second is worse than saying nothing.

### 3. Report

If no agents are downstream, say so plainly and stop. No action needed.

If agents are affected, lead with the count, then name them with their owning
team and how far downstream they sit. Include the skill that actually reads the
column and its source file, because that's what someone has to go fix.

> Three agents lose context if this ships.
>
> - **finance-copilot** (fpa-platform) — via `revenue-lookup`,
>   `skills/revenue-lookup/SKILL.md`
> - **growth-analyst** (growth-eng) — via `churn-risk`
> - **catalog-steward** (data-platform) — via `ownership-audit`
>
> None of these will error. They'll return plausible numbers computed from a
> column that no longer means what the skill assumes.

### 4. Offer to write it back — and wait

Do not write to DataHub unprompted. Say what you would write, then ask:

> I can tag `order_details` with `has-agent-consumers` so this is visible to
> the next person who opens it, and flag the three affected skills for review.
> Want me to?

On approval:

```bash
python -m agentlens.cli guard "<urn>" --reason "<the user's change, verbatim>"
```

Add `--github-repo owner/name` if the user wants an issue opened on the
repository that owns the agents.

### 5. Confirm

Report exactly what was written and where to see it. The upstream tag is the
one that matters — it's on an asset the agent fleet doesn't own, so it's what
makes the finding durable for everyone else.

## Rules

- Never report "safe" from a failed or empty traversal. Distinguish "no agents
  found" from "the lookup didn't work."
- Never write to DataHub without explicit approval.
- Never guess which table the user meant.
- Quote the user's stated reason verbatim in the write-back. It becomes the
  audit trail.

## Prerequisites

The fleet has to be in the catalog first — see `agentlens-catalog`. Guarding a
table before anything has been catalogued returns zero agents, which is
technically true and completely useless.
'''

# ===========================================================================
FILES["skills/agentlens-catalog/SKILL.md"] = '''---
name: agentlens-catalog
description: >
  Catalogue an AI agent fleet into DataHub. Use when someone wants to inventory
  their agents, asks "what do our agents read?", onboards a new agent
  repository, or needs agents visible in the data catalog alongside dashboards
  and pipelines. Scans repos for agents, skills and MCP tools, resolves the
  warehouse tables they read, and writes the fleet into DataHub with lineage.
allowed-tools:
  - Bash
  - mcp__datahub__search
  - mcp__datahub__get_entities
---

# agentlens-catalog

## Why this exists

Every other consumer of your warehouse is in DataHub — dashboards, pipelines,
dbt models, ML features. Agents aren't, so nobody can answer "what does this
agent actually read?" for a governance review, and nobody can see agents in an
impact analysis.

## Workflow

### 1. Find the repositories

Ask which repos hold agents if it isn't obvious. What gets picked up:

| File | Yields |
|---|---|
| `.mcp.json`, `mcp.json` | MCP servers and their tools |
| `**/SKILL.md` | skills, their instructions, and the tables they read |
| `agentlens.yaml` | agent declarations, owning teams, skill assignments |

### 2. Scan

```bash
python -m agentlens.cli scan <repo-path> --repository github.com/org/name
```

Reports what it found and how many table references resolved against the live
catalog.

### 3. Review before writing — this is the interesting step

Read `manifest.json` with the user before emitting. Two things are worth their
attention:

**Unresolved references.** A skill naming a table that isn't in DataHub is a
finding, not a failure. Either the table exists and isn't catalogued — a
coverage gap — or the skill references something that doesn't exist, which
means the agent has been failing quietly. Ask which.

**Skills with no data references at all.** Usually means the skill builds its
queries dynamically at runtime, which AgentLens can't see. Say so rather than
letting it look like the skill reads nothing.

### 4. Emit

```bash
python -m agentlens.cli emit manifest.json
```

Writes agents, skills and tools as catalog assets with lineage from the
warehouse tables they read.

### 5. Report

Tell the user what's now browsable, and give them one concrete next step:

> 3 agents, 5 skills and 6 MCP tools are in the catalog. Browse them under
> `agentlens` in DataHub, or open any table they read and check the Lineage tab
> — skills sit one hop downstream, agents two.
>
> Before your next schema change, run `agentlens-guard` on the table first.

## Rules

- Show the manifest before emitting. It's the user's fleet; they should see how
  it was interpreted.
- Report unresolved references explicitly. Silently dropping them makes the
  graph look more complete than it is, which is the failure mode this whole
  tool exists to prevent.
- Re-scan after the agent repos change. The catalog is a snapshot, not a live
  view.
'''

# ===========================================================================
FILES[".mcp.json"] = '''{
  "mcpServers": {
    "datahub": {
      "command": "uvx",
      "args": ["mcp-server-datahub@latest"],
      "tools": ["search", "get_lineage", "get_entities", "list_schema_fields"]
    }
  }
}
'''


def patch_cli() -> bool:
    """Add --format json to `impact` so an agent can parse the result."""
    path = "agentlens/cli.py"
    with open(path, encoding="utf-8") as fh:
        cli = fh.read()

    if '"--format"' in cli:
        print("  ok     cli: --format (already applied)")
        return True

    old = "def cmd_impact(args) -> int:\n    report = blast_radius(args.urn, max_hops=args.hops)\n    print(render(report))"
    new = (
        "def cmd_impact(args) -> int:\n"
        "    report = blast_radius(args.urn, max_hops=args.hops)\n"
        '    if getattr(args, "format", "text") == "json":\n'
        "        print(json.dumps(report, indent=2))\n"
        "    else:\n"
        "        print(render(report))"
    )
    if old not in cli:
        print("  MISS   cli: cmd_impact body")
        return False
    cli = cli.replace(old, new, 1)

    old_arg = '    p.add_argument("--html", help="also write a self-contained HTML report")\n    p.set_defaults(func=cmd_impact)'
    new_arg = (
        '    p.add_argument("--html", help="also write a self-contained HTML report")\n'
        '    p.add_argument(\n'
        '        "--format",\n'
        '        choices=["text", "json"],\n'
        '        default="text",\n'
        '        help="json prints the raw report to stdout, for agents to parse",\n'
        "    )\n"
        "    p.set_defaults(func=cmd_impact)"
    )
    if old_arg not in cli:
        print("  MISS   cli: impact arguments")
        return False
    cli = cli.replace(old_arg, new_arg, 1)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(cli)
    print("  patch  cli: --format json on impact")
    return True


def main() -> int:
    if not os.path.isdir("agentlens"):
        print("Run this from inside agentlens-project/.")
        return 1

    ok = patch_cli()

    for rel, content in FILES.items():
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        if os.path.exists(rel) and rel == ".mcp.json":
            print(f"  skip   {rel} (exists)")
            continue
        with open(rel, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"  write  {rel}")

    # a test that the skills are scannable by AgentLens itself
    test = "tests/test_self_scan.py"
    if not os.path.exists(test):
        with open(test, "w", encoding="utf-8") as fh:
            fh.write('''"""AgentLens can catalogue its own skills. No DataHub required."""

import os

from agentlens.scanner import scan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_finds_its_own_skills():
    manifest = scan(ROOT, "github.com/agentlens/agentlens")
    ids = {s.id for s in manifest.skills}
    assert "agentlens-guard" in ids
    assert "agentlens-catalog" in ids


def test_finds_its_own_mcp_tools():
    manifest = scan(ROOT, "github.com/agentlens/agentlens")
    names = {t.name for t in manifest.tools}
    assert "get_lineage" in names


def test_skills_declare_their_tools():
    manifest = scan(ROOT, "github.com/agentlens/agentlens")
    guard = next(s for s in manifest.skills if s.id == "agentlens-guard")
    assert any("lineage" in t for t in guard.tools)
''')
        print(f"  write  {test}")

    print()
    print("""  Done.

Try the loop an agent would run:

    python -m agentlens.cli impact "<urn>" --format json

Then point AgentLens at itself - it should find its own two skills:

    python -m agentlens.cli scan . --repository github.com/<you>/agentlens
""")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
