# PR 2 of 2 — depends on PR 1 to run locally

**Title**

```
feat(ui): add an Agents tab to the dataset profile
```

**Target:** `datahub-project/datahub` `master`
**Branch:** one commit, `f044e81`

> Suggest opening this as a **draft** and asking for design feedback before
> review time is spent on it. The open questions at the bottom are real, and one
> of them may change the shape of the whole thing.

---

## Body

### The problem

Dashboards have lineage. Agents don't.

When you drop a column a dashboard depends on, the dashboard *errors* — someone
gets paged, someone notices. When an agent's skill says "sum `line_total` from
`analytics.order_details`" and that column changes meaning, the agent doesn't
error. It confidently returns a number that's wrong.

Lineage tells you *what* is downstream. It cannot tell you *whether the change
matters*, and for agents that gap is wide.

### What this adds

An **Agents** tab on the dataset profile, next to Lineage. It simulates a schema
change — drop a column, rename the table, drop the table — and classifies every
downstream agent and skill as **breaks / degrades / unchanged**.

Dropping `analytics.order_details.line_total` against a demo fleet of 3 agents
and 5 skills:

| | |
|---|---|
| Lineage-only impact analysis flags | **7** consumers |
| This tab flags | **3** |

The other four read the same table but never name `line_total` in their skill
text. Four teams would have been chased for nothing. Rename or drop the whole
table and all 7 break — correctly, because then the text doesn't save you.

### How it fits in

No fork, no patched image, no gradle changes:

- 4 new files under `entityV2/dataset/profile/AgentImpact/`
- one tab entry in `DatasetEntity.tsx` `getProfileTabs()`
- one `/agentlens` proxy entry in `vite.config.ts`

Visibility uses the existing `display.visible` predicate, keyed on a
`has-agent-consumers` tag. The tag rides in on `globalTags`, which the dataset
query already fetches, so the gate costs no extra request and the tab appears on
exactly the tables an agent fleet reads and nowhere else.

### Checklist

- [x] The PR conforms to DataHub's Contributing Guideline (particularly PR Title Format)
- [ ] Links to related issues — none filed yet
- [ ] **Tests for the changes have been added/updated — not yet.** 682 lines of new TSX/TS with no vitest or Playwright coverage. Will add before this leaves draft; flagging rather than hiding it.
- [ ] **Docs — not yet.** A usage guide is needed if this lands as a feature.
- [ ] Updating DataHub entry — not applicable, purely additive, no breaking change

### Known gaps, stated up front

1. **i18n.** The tab name is the literal `'Agents'` while every neighbouring tab
   uses `i18next.t(...)`, and `AgentImpactTab.tsx` carries a blanket
   `eslint-disable i18next/no-literal-string`. Needs real translation keys —
   `feat(i18n)` is the single most common scope in this directory's history, so
   I assume this matters here.
2. **No frontend tests**, as above.
3. **`./gradlew :datahub-web-react:yarnLint` has not been run** against this
   branch.

Styling does follow `CLAUDE.md` — semantic theme tokens throughout, no
hardcoded hex, no `REDESIGN_COLORS` or `ANTD_GRAY`.

### Open questions for maintainers

**1. Where should the data come from?** Today the tab fetches
`/agentlens/payload.json` from a local sidecar through a vite proxy. That is fine
for a demo and wrong for a product — it needs a dev server and a second process,
and it is why this is a draft. The upstream tool already writes its findings back
into DataHub as tags and aspects, so the data *is* in the catalog; the tab just
isn't reading it from there. **What is the right way to surface derived,
third-party analysis on an entity page?** That answer probably determines
everything else here.

**2. Entity modelling.** The agents, skills and tools this reads are emitted as
`dataset` entities on a synthetic platform, distinguished by subtype (`AI Agent`,
`Agent Skill`). That was deliberate — DataHub's own lineage UI renders the graph
unmodified and the Summary panel says "DOWNSTREAM — Used by 1 ai agent" with no
change on my side. But they are not datasets. **Should these be first-class
entity types, or is there an existing entity I should map onto?**

**3. Is the heuristic acceptable?** "Names `line_total` in its text" is substring
matching over skill markdown. It is the core insight and also the weakest link —
false negatives on aliased columns, false positives on coincidental words.
**Is a signal that's right most of the time useful for governance, or does it
need to be sound first?**

### Context

The tab is the UI surface of a larger tool that scans agent repositories and
emits agents, skills and MCP tools into DataHub with lineage back to the tables
they read. Full write-up, source and a cold-start runbook:
https://github.com/AKSHAJ-SHELL/Datahub_submission
