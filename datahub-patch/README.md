# The DataHub-side changes

Everything AgentLens adds to DataHub's own UI is in these two patches. They
apply to `datahub-project/datahub` at commit **`ee7a980`** (master, 2026-07-31).

```bash
cd /path/to/datahub
git checkout -b agentlens-demo ee7a980
git am /path/to/agentlens-project/datahub-patch/*.patch
```

| Patch | What it is | Files it as |
|---|---|---|
| `0001-fix-ui-...` | **Unrelated upstream bug.** `useSetAppTheme.tsx` imports `./conf/theme/...` but the themes live at `src/conf/theme/`, so the path needs `../`. esbuild cannot resolve the glob and `vite` refuses to boot. One character. Included only because you cannot run the dev server without it. | `PR-1-theme-fix.md` |
| `0002-feat-ui-...` | The Agents tab. 4 new files under `entityV2/dataset/profile/AgentImpact/`, the tab entry in `DatasetEntity.tsx`, and a `/agentlens` proxy entry in `vite.config.ts`. | `PR-2-agents-tab.md` |

## These are two PRs, not one

`docs/CONTRIBUTING.md`: *"PRs are squashed and merged, resulting in a single
commit with the PR title as the commit message."* Bundling both commits into one
PR would squash an unrelated one-character bug fix into a feature commit. File
them separately, PR 1 first.

Titles use the `ui` scope, which is what upstream actually uses for
`datahub-web-react` changes — `fix(ui)` and `feat(ui)` dominate that directory's
history, while `web-react` appears zero times.

## File against the parent repo, not the fork

`acryldata/datahub` is a **fork** of `datahub-project/datahub` (34 stars vs
12.4k). Its open PRs are dependabot bumps and "sync OSS upstream master" — it is
Acryl's sync fork, not where community feature PRs go. Target
`datahub-project/datahub` `master`.

No fork, no patched Docker image, no gradle changes. The tab is registered
through the existing `getProfileTabs()` mechanism and gated through the existing
`display.visible` predicate.

## Why the tab is gated on a tag

`display.visible` keys off the `has-agent-consumers` tag that `agentlens guard`
writes. The tag arrives on `globalTags`, which the dataset query already
fetches, so the gate costs no extra request and the tab appears on exactly the
tables an agent fleet reads — and nowhere else.

## Where its data comes from

`agentlens serve` runs locally and exposes `/payload.json`. The `vite.config.ts`
entry proxies `/agentlens` to it so the fetch is same-origin.

**This is the least settled part of the design and the main thing we want
opinions on.** A local sidecar is fine for a demo and wrong for a product. See
"Open questions" in the top-level `SUBMISSION.md`.
