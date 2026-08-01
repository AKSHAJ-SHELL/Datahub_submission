# AgentLens — project state

Everything built, everything verified, everything left. Written 29 July 2026.

**Submission deadline: 10 August 2026, 5:00pm EDT.** Twelve days.

---

## 1. What this is, in one paragraph

Every consumer of your warehouse is catalogued — dashboards, pipelines, dbt
models — except your AI agents. So when someone drops a column, impact analysis
is blind to the consumer that fails most quietly: a dashboard errors and pages
someone, an agent returns a confident wrong number and nobody finds out until a
decision has been made on it. AgentLens scans agent repositories, derives what
each skill actually reads from `SKILL.md` and `.mcp.json`, resolves those
references against the live catalog, writes the fleet into DataHub with real
lineage, and then answers what nobody can answer today: *if I change this
table, which agents lose context?*

---

## 2. Hackathon fit

**Challenge 1 — "Agents that do real work."** The brief: *"the agent should read
DataHub to understand what's connected to what, take action, and write results
back so the next person or agent inherits the context."* `guard` prints
`[1/3] READ`, `[2/3] DECIDE`, `[3/3] WRITE BACK` and closes with "The next
person or agent to open these assets in DataHub inherits this context."

| Judging criterion | Where we stand |
|---|---|
| Use of DataHub | Strong on the context graph and write-back. **Weak on MCP Server / Agent Context Kit** — see §6. |
| Technical execution | Strong. Runs end to end, 100+ tests, ruff and mypy clean. |
| Originality | Strong. Two capabilities verified absent from DataHub entirely. |
| Real-world usefulness | Strong. Pre-merge check, CI-gateable, writes findings back. |
| Submission quality | **Weakest — but improving.** Still no video. The UI is now an entity tab inside DataHub, not a separate page (§5). |
| Bonus — OSS contribution | Issue written, not filed. |

---

## 3. What is built

Nine commands. All verified working end to end against a live v1.6.0
quickstart with the `showcase-ecommerce` datapack.

| Command | Does |
|---|---|
| `scan` | Walk a repo → agents, skills, MCP tools, table references |
| `emit` | Write the fleet into DataHub as datasets with lineage |
| `impact` | Downstream blast radius from a table |
| `guard` | impact + decide + write findings back (tags, GitHub issue) |
| `drift` | Re-scan and diff against the catalog |
| `sandbox` | Simulate a change that hasn't happened |
| `explore` | Build a self-contained clickable HTML view |
| `serve` | Serve the explorer on localhost, rebuilt per request |
| `link` | Put the explorer in DataHub's entity sidebar |

### Modules

| File | Job |
|---|---|
| `scanner.py` | Parse `.mcp.json`, `SKILL.md`, `agentlens.yaml`; regex table refs with SQL alias resolution |
| `model.py` | `DataRef`, `Tool`, `Skill`, `Agent`, `Manifest` |
| `resolver.py` | Name → URN against the live catalog, scored exact → suffix → leaf → substring |
| `emitter.py` | Nodes and `upstreamLineage` into DataHub; owns the URN scheme |
| `impact.py` | `blast_radius()` — cache-free downstream walk |
| `actions.py` | Write-back: tags, deprecation, GitHub issues |
| `drift.py` | `expected_state()` and the catalog diff |
| `sandbox.py` | `fork()` + `simulate()` — the what-if engine |
| `explorer.py` | Builds the self-contained HTML |
| `server.py` | stdlib HTTP server + `institutionalMemory` linking |
| `report.py` | HTML impact report |
| `cli.py` | All nine commands |

### Demo numbers (reproducible via `./demo.sh`)

- 3 agents, 5 skills, 6 MCP tools scanned
- **9 table references extracted, 8 resolved.** The 9th, `analytics.events`,
  genuinely isn't in the showcase data
- 14 entities emitted
- `guard` on `analytics.order_details`: 3 agents, 4 skills degrade
- `sandbox --drop-column line_total` on the same table: **2 break, 1 degrade,
  4 unaffected**
- `drift` after a one-line skill edit: `CHANGED` + `REF +` + `REF -`, exit 1

### Quality

- ruff clean, mypy clean across 13 source files
- Tests: `test_model`, `test_scanner`, `test_report`, `test_integration`,
  `test_self_scan`, `test_impact_safety`, `test_drift`, `test_sandbox`,
  `test_explorer`, `test_server`
- The four newest suites are pure functions over hand-built inputs — no
  DataHub, no network — because the failures they guard are silent ones
- Two packaged skills: `agentlens-catalog`, `agentlens-guard`, plus `.mcp.json`

---

## 4. What we established about DataHub

All verified against source, not documentation.

**The agent metamodel is merged but unreleased.** `aiAgent`, `agentSkill`,
`api` landed in [#18478](https://github.com/datahub-project/datahub/pull/18478)
on 19 July 2026. v1.6.0 was tagged 21 May and is still the latest release.
`entity-registry.yml` has 71 entities on master, 64 on v1.6.0; the difference
is exactly `aiAgent`, `agentSkill`, `api`, `metric`, `repository`,
`semanticModel`, `service`.

**Nothing upstream derives agent metadata from source.** `SKILL.md` appears
only in docstrings and a `path` field — nothing opens one. `.mcp.json` appears
nowhere in the repo. No `scan`/`discover`/`crawl` functions. `agentSkillInfo.instructions`
is documented as *"the markdown body of the skill's SKILL.md file"* and every
example hand-types it. `fx_risk_scoring_agent.py` hardcodes a Python list of
dataset URNs, and `materialize_consumed_datasets.py` exists to invent
placeholder `datasetProperties` because those URNs point at nothing real.

**DataHub has no what-if.** Checked all 71 entity types and the docs — no
sandbox, no branch, no simulation. `docs/act-on-metadata/impact-analysis.md` is
read-only and always about the graph as it stands.

**The docs site publishes unreleased content under the last release's label.**
`docs-website/docusaurus.config.js` sets `versions: { current: { label: "1.6.0",
banner: 'none' } }` while building from master. Not a PR — the label is
deliberately maintained (#15486 reverted versioned docs; #16232 and #17987 bump
it at release). Filed as an issue instead.

**Lineage reads are backed by two different caches.** `Dataset.lineage` has no
`skipCache` and no `searchFlags` to borrow one from; `searchAcrossLineage` has
one but is a different cache. Measured on GMS v1.5.0.6 by **ogze** in the
DataHub Slack with the criterion fixed before the run: after removing lineage,
`searchAcrossLineage` was stale past 120s while `Dataset.lineage` was stale at
t+0 and clean from t+30. This was a live bug in AgentLens — `emit` then `guard`
is t+0 — and a stale read made it print *"No agents downstream. Safe to
change."* Now fixed: reads go through stored `upstreamLineage` aspects, and
`render()` refuses to claim safety from a traversal it couldn't complete.

---

## 5. What's left — priority order

### ~~P0 · Integrate into the DataHub UI~~ — done, 29 July

Built as an **Agents** tab on the dataset profile, next to Lineage. Steps 1–5
of the §6 plan all landed. What actually exists:

| File in `../datahub/datahub-web-react` | |
|---|---|
| `.../profile/AgentImpact/AgentImpactTab.tsx` | the tab — controls, counts, effect rows, the comparison callout |
| `.../profile/AgentImpact/simulate.ts` | TS port of `sandbox.py::simulate()` |
| `.../profile/AgentImpact/types.ts` | the payload shape |
| `.../profile/AgentImpact/useAgentLensPayload.ts` | the fetch, plus the tag urn constant |
| `.../profile/AgentImpact/hasAgentConsumers.ts` | the `display.visible` predicate |
| `dataset/DatasetEntity.tsx` | tab entry in `getProfileTabs()` |
| `vite.config.ts` | the `/agentlens` proxy |
| `src/app/useSetAppTheme.tsx` | upstream bug fix — see below |

Plus `/payload.json` on `agentlens serve` (`server.py`), and the banner line in
`cli.py`.

Verified: `tsc --noEmit` is clean across the whole project (0 errors), all five
new modules transform and resolve through the dev server, the payload reaches
the browser through the proxy at `localhost:3000/agentlens/payload.json`, and
the three table urns in `manifest.json` match the live catalog exactly with
`has-agent-consumers` already on `order_details`. **Not yet eyeballed in a
browser** — that's the one remaining check, and it's §8 of the RUNBOOK.

The dev-environment setup was more than `yarn install`: no yarn on the machine
(use `npx yarn@1.22.22`), and two generated trees are missing from a fresh
checkout — `node scripts/generate-lazy-icon-stubs.js` (1512 stubs) and
`yarn run generate` (graphql codegen → `types.generated.ts`). Both one-shot.

**A second OSS contribution fell out of it.** `vite` would not start at all:
`src/app/useSetAppTheme.tsx` dynamically imports `` `./conf/theme/${id}` `` but
the themes are at `src/conf/theme/`, so from `src/app/` the path needs `../`.
esbuild's dep scan can't resolve the glob and fails the boot with *"Could not
resolve import('./conf/theme/\*\*/\*')"*. One character, reproducible on a clean
checkout of master, and it blocks every new frontend contributor. Better first
PR than the docs label — smaller, unambiguous, and no RFC.

### P0 · Demo video, under 3 minutes

A mandatory submission artifact and judging criterion 5. Suggested cut:

1. **0:00–0:25** The asymmetry. Dashboard errors loudly, agent answers
   confidently and wrongly.
2. **0:25–0:55** `scan` + `emit`, then the DataHub screenshot: *"DOWNSTREAM —
   Used by 1 ai agent."* DataHub's own words, from the subtype we emit.
3. **0:55–1:40** The money shot, in the DataHub UI. `guard` says 3 agents and 4
   skills degrade. The sandbox tab says 2 break, 1 degrade, 4 unaffected. Both
   correct — lineage has no column in it.
4. **1:40–2:10** Write-back. The Snowflake table now carries
   `has-agent-consumers`, on an asset we don't own.
5. **2:10–2:40** `drift`, exit 1, "this fails a CI check."
6. **2:40–3:00** What we found upstream, and the issue filed.

### P1 · Submission text

Repo is already Apache 2.0. README is current. Needs the Devpost description
and the examples folder tidied.

### P2 · Honest limitation to add to the README

Leaf matching resolved `analytics.customers` → `order_entry.customers`. Correct
here, but in a warehouse where two schemas both have `customers` that's a
confident wrong match, and unlike an unresolved reference nothing prints.

### Deferred until after the deadline

- **File the docs issue.** Body written in `issue-docs-version-label.md`.
- **The scanner contribution.** DataHub routes substantial features through
  `docs/rfc.md`, and their developing guide wants work done in their gradle dev
  environment. Both are post-hackathon.

---

## 6. The UI integration plan — executed 29 July

Kept as written, because it turned out accurate and it's the record of why the
approach works. Deviations from it are noted in §5.

### Why it's tractable

`datahub-web-react` is a Vite app whose dev server **proxies to
`http://localhost:9002`** — your running quickstart. So you run the React dev
server in front of the stack you already have. No Docker image rebuild, no
gradle, no forked backend. And it's the dev environment DataHub's own guide
describes, so the work carries over to a real contribution later.

```bash
cd ~/Desktop/DataHub/datahub/datahub-web-react
yarn install          # first time only, slow
yarn start            # Vite dev server, proxying to :9002
```

### Where the tab plugs in

Entity tabs are plain objects in an array. In
`datahub-web-react/src/app/entityV2/dataset/DatasetEntity.tsx`,
`getProfileTabs()` returns entries shaped:

```tsx
{
    name: 'Agents',
    component: AgentImpactTab,
    icon: RobotOutlined,
    display: {
        visible: (_, dataset) => hasAgentConsumers(dataset),
        enabled: (_, dataset) => hasAgentConsumers(dataset),
    },
}
```

`display.visible` is the elegant part: key it off the `has-agent-consumers`
tag AgentLens already writes, so the tab appears **only on tables agents
actually read**. The tag is in `globalTags`, already fetched by the existing
dataset query — no new GraphQL needed.

### Where the data comes from

Add a proxy entry to `datahub-web-react/vite.config.ts` alongside the existing
`:9002` one:

```ts
'/agentlens': {
    target: process.env.REACT_APP_AGENTLENS_TARGET || 'http://localhost:8000',
    changeOrigin: true,
    rewrite: (p) => p.replace(/^\/agentlens/, ''),
}
```

Then `agentlens serve` grows a `GET /payload.json` returning what `explore`
already embeds, and the tab does `fetch('/agentlens/payload.json')`. Same
origin, so no CORS, and it mirrors how DataHub already proxies to the frontend.

### The tab component

A new file under `src/app/entityV2/dataset/profile/AgentImpact/`. It's the
explorer's logic, in DataHub's own components:

- change-kind selector → their `Button` group
- column chips → their `Tag`
- effect rows → their `Table` or list rows, coloured by severity
- the comparison line — *"lineage flags 7, AgentLens says 3"* — as a callout

The simulation is the same ~40 lines already mirrored in
`explorer.py::_SIMULATE_JS`, so it ports directly.

### Order of work

1. `yarn install && yarn start`, confirm the unmodified UI loads at :3000
   against your quickstart. **Don't skip this** — if the dev server doesn't
   come up, nothing else matters and you want to know tonight.
2. Add `/payload.json` to `agentlens serve`.
3. Add the vite proxy entry, confirm `fetch('/agentlens/payload.json')` works
   from the browser console.
4. Add a stub tab that renders "hello" — confirm it shows up on
   `order_details` and *doesn't* show on a table with no agents.
5. Build the real tab.

Steps 1–4 are the risky ones and they're all small. Step 5 is the visible one
and it's mostly porting.

### What to keep

`explore`, `serve` and `link` stay. The standalone HTML is what a judge can
open without running anything, and the sidebar link is a genuine "no fork"
story. The tab is the product-looking version on top.

---

## 7. Repo map

```
agentlens-project/
├── agentlens/          scanner model resolver emitter impact actions
│                       drift sandbox explorer server report cli
├── demo-repo/          3 agents, 5 skills, 2 MCP servers
├── skills/             agentlens-catalog, agentlens-guard
├── tests/              10 suites
├── tools/probe_v2.py   the entity-registry probe
├── docs/               used-by-1-ai-agent.png
├── examples/           blast-radius.html, guard-run.json, explorer.html
├── demo.sh             the whole thing end to end
├── README.md
├── RUNBOOK.md          cold start from stopped Docker
└── add_*.py fix_*.py   one-shot generators, kept for reproducibility

../                     issue-docs-version-label.md, PR-docs-version-label.md
../datahub/             DataHub source, for the UI work and the later PR
```

---

## 8. The one-paragraph pitch

> DataHub shipped the agent metamodel to master eleven days ago. It has a field
> documented as "the markdown body of the skill's `SKILL.md`" — and nothing in
> the codebase reads a `SKILL.md`. Its own demo agent hardcodes a Python list
> of dataset URNs, then ships a second script to invent placeholder datasets
> because those URNs point at nothing.
>
> AgentLens is the scanner. Point it at your agent repos and it derives the
> fleet from source, resolves it against your live catalog, and answers the
> question the catalog still can't: if I drop this column, which agents lose
> context? Not which agents *read the table* — DataHub can tell you that, and
> on our demo it says seven. Which ones actually name the column. Three.
>
> Because a dashboard that loses its upstream errors, and someone gets paged.
> An agent doesn't error. It answers confidently and wrongly, and nobody finds
> out until a decision has been made on the answer.
