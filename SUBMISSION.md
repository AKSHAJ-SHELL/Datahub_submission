# AgentLens — lineage for AI agents

Asking for a read on this before taking it further. It works end to end today,
but several of the design decisions were made to get a demo standing, and I'd
rather hear now than after building on them.

---

## The problem

Your dashboards have lineage. Your agents don't.

When you drop a column and a dashboard depends on it, the dashboard *errors*.
Someone gets paged. Someone notices.

When an agent's skill says "sum `line_total` from `analytics.order_details`" and
that column quietly changes meaning, the agent doesn't error. It confidently
returns a number that's wrong, and nobody finds out until a decision has been
made on it.

Nobody can currently answer: **if I change this table, which agents break?**

## What it does

Scans an agent repository (`.mcp.json`, `SKILL.md`, `agentlens.yaml`), resolves
the table references it finds against the live catalog, and emits every agent,
skill and MCP tool into DataHub as a catalog asset with real lineage back to the
warehouse tables they read. Then it walks that lineage to answer the question.

Five commands do the work:

```bash
agentlens scan    demo-repo --repository github.com/acme/data-agents
agentlens emit    manifest.json
agentlens guard   "<urn>" --reason "dropping line_total"
agentlens sandbox order_details --drop-column line_total
agentlens drift   demo-repo --repository github.com/acme/data-agents
```

`guard` runs the loop DataHub describes — **read** the graph, **decide**, **write
results back**. It tags the upstream table `has-agent-consumers` and the
downstream assets `agent-context-review`, so the next person or agent to open
those assets inherits the context.

`drift` exits non-zero, so it works as a CI check.

## The part that isn't just lineage

Lineage tells you *what* is downstream. It cannot tell you *whether the change
matters*, and for agents the gap is large.

Dropping `analytics.order_details.line_total` in the demo fleet:

| | |
|---|---|
| Lineage-only impact analysis flags | **7** consumers |
| AgentLens flags | **3** |

The other four read the same table but never name `line_total` in their skill
text. `churn-risk` and `ownership-audit` come back UNCHANGED. Four teams would
have been chased for nothing.

Rename or drop the whole table and all 7 break — correctly, because then the
text doesn't save you. The column case is the interesting one.

## Inside DataHub's UI

Two surfaces, neither of which forks DataHub or patches a Docker image:

1. **A sidebar link** on every table the fleet reads, written as an
   institutional-memory link, deep-linking to a simulator for that table.
2. **An "Agents" tab** on the dataset profile, next to Lineage, in DataHub's own
   components — registered through the existing `getProfileTabs()` mechanism and
   gated through the existing `display.visible` predicate. It's an interactive
   simulator: pick a change, see breaks/degrades/unchanged live, with the
   equivalent CLI command rendered underneath.

The tab appears on exactly the tables an agent fleet reads and nowhere else,
because `display.visible` keys off the `has-agent-consumers` tag that `guard`
writes, and that tag rides in on `globalTags` which the dataset query already
fetches — no extra request.

The DataHub-side diff is 4 files. See `datahub-patch/`.

## Status

- 102 tests pass (1 skips without GMS), `ruff` and `mypy` clean on 13 files
- Runs against `datahub-project/datahub` at `ee7a980` with the matching
  `sha-ee7a980` quickstart images
- Cold-start instructions in `RUNBOOK.md`

---

## Where I think the gaps are

Listing these because they're the parts I'd most like to be told I got wrong.

**1. How the tab gets its data.** `agentlens serve` runs as a local sidecar and
the tab fetches `/agentlens/payload.json` through a vite proxy. This is fine for
a demo and wrong for a product — it means a dev server and a second process.
`guard` already writes its findings back as tags and aspects, so the data is
*in* DataHub. The tab should probably read that through GraphQL like every other
tab does. **What's the right way to surface derived, third-party analysis on an
entity page?**

**2. Entity modelling.** Agents, skills and tools are emitted as `dataset`
entities on a synthetic `agentlens` platform, distinguished by subtype (`AI
Agent`, `Agent Skill`). That was deliberate — it means DataHub's own lineage UI
renders the graph unmodified, and the Summary panel says "DOWNSTREAM — Used by 1
ai agent" without any change on my side. But they aren't datasets. **Should
these be first-class entity types? Is there an existing entity I should be
mapping onto instead?**

**3. The matching is textual.** "Names `line_total` in its text" is substring
matching over skill markdown. That's the core insight of the project and also
its weakest link — it will produce false negatives on aliased columns and false
positives on coincidental words. Real instruction parsing is the obvious next
step. **Is a heuristic that's right most of the time acceptable for a governance
signal, or does it need to be sound before it's useful?**

**4. Reference resolution is heuristic.** 8 of 9 references resolve in the demo;
the resolver leaf-matches table names against the catalog and can pick the wrong
schema when names collide. The 9th (`analytics.events`) genuinely isn't in the
showcase data, and `drift` reports it as a governance finding rather than
swallowing it — which I think is right, but it's a judgment call.

**5. Not deployment-ready.** The tab only exists under the vite dev server. A
real version needs to be in the frontend image, which means the payload question
(1) has to be answered first.

## Known PR-readiness issues

Separate from design — these I already know are wrong:

- **i18n.** The tab name is the literal `'Agents'` while every neighbouring tab
  uses `i18next.t(...)`, and `AgentImpactTab.tsx` carries a blanket
  `eslint-disable i18next/no-literal-string`. Needs proper translation keys.
- **No frontend tests.** 682 lines of new TSX/TS with no Playwright or vitest
  coverage. The Python side is well covered; this isn't.
- **Gradle checks not run.** `./gradlew :datahub-web-react:yarnLint` hasn't been
  run against this branch.
- **Repo hygiene.** The root still contains the `add_*.py` / `fix_*.py`
  scaffolding scripts used to build the project incrementally. They're not part
  of the deliverable and should be removed or moved.
- **`useSetAppTheme.tsx` is unrelated.** It's a genuine upstream bug that blocks
  `vite` from booting; it's in the patch set only because you can't run the dev
  server without it. It should be filed and fixed separately.

## What I'm asking

1. Is the question worth answering — is "which agents break if I change this
   table" something DataHub should have an answer to?
2. If yes, is *this* the right shape for it, or is it working against the grain
   of the metadata model?
3. Which of the five gaps above would you fix first?
4. Anything here that would be a blocker in review that I haven't listed?
