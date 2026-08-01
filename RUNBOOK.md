# AgentLens — cold start runbook

From a stopped Docker to the explorer inside DataHub's UI. Roughly 10 minutes,
most of it waiting for containers.

Each step has a **check**. If a check fails, stop there — the next step will
fail in a way that's harder to read.

---

## 0. Two files you need first

The bridge to your Mac dropped before these landed, so download them from the
chat into `~/Desktop/DataHub/agentlens-project/`:

- `add_explorer.py` — **the newer one**, with deep-link support. The copy on
  disk predates it, and `add_serve.py` will refuse to run against the old one.
- `add_serve.py` — new file, not on disk at all.

**Check:**

```bash
cd ~/Desktop/DataHub/agentlens-project
grep -c URLSearchParams add_explorer.py    # must be >= 1
ls add_serve.py                            # must exist
```

If `grep` returns 0, you have the old `add_explorer.py`.

---

## 1. Start Docker, then DataHub

Open Docker Desktop and wait for the whale to stop animating. Then:

```bash
docker compose --profile quickstart \
  -f ~/.datahub/quickstart/docker-compose.ee7a980.yml \
  --env-file ~/.datahub/quickstart/agentlens-stack.env \
  -p datahub up -d
```

**Not `datahub docker quickstart --version v1.6.0`.** The stack is pinned to the
published `sha-ee7a980` images, which are built from the exact commit this
checkout sits on (`git log -1` → `ee7a980`). That match is load-bearing — see
section 8. `--env-file` supplies `DATAHUB_TOKEN_SERVICE_SIGNING_KEY` and
`DATAHUB_TOKEN_SERVICE_SALT`, which this compose file requires and has no
defaults for; without it `system-update` dies with *"authentication
.tokenService.signingKey must be set"* and nothing else starts.

Takes a few minutes.

**Check:**

```bash
curl -s http://localhost:8080/config | python3 -m json.tool | head -20
```

Should print JSON. If it hangs or refuses, DataHub isn't up yet — wait and
retry rather than moving on.

---

## 2. Confirm the catalog still has data

```bash
curl -s -X POST http://localhost:8080/api/graphql \
  -H 'Content-Type: application/json' \
  -d '{"query":"query($i: SearchAcrossEntitiesInput!){searchAcrossEntities(input:$i){total}}",
       "variables":{"i":{"types":["DATASET"],"query":"*","start":0,"count":1}}}'
```

**Check:** `"total"` should be **67** before you run `demo.sh`, and **81** after
— the 14 extra are the AgentLens agents, skills and tools that `emit` writes.

If it's 0, the volumes were wiped and you need the sample data again:

```bash
datahub init      # press Enter twice: default host, blank token
datahub datapack load showcase-ecommerce
```

`datahub init` asks for a **host** — press Enter for `http://localhost:8080`.
The `datahub / datahub` in the quickstart banner is the frontend login at
:9002, not the answer to either prompt.

---

## 3. Environment

`demo.sh` sets these itself, but the CLI commands you run by hand need them:

```bash
export DATAHUB_GMS_URL=http://localhost:8080
export DATAHUB_GMS_TOKEN=dummy       # any string; auth is off on quickstart
```

---

## 4. Install the two new commands

```bash
python add_explorer.py     # rewrites explorer.py with deep links
python add_serve.py        # adds serve + link
```

**Check:** you should see `write agentlens/explorer.py`, then
`write agentlens/server.py` and `patch cli: serve and link commands`.

If `add_serve.py` says *"Re-run add_explorer.py first"*, you're still on the
old `add_explorer.py` — go back to step 0.

---

## 5. Verify the build

```bash
ruff check . && mypy agentlens && pytest -q
```

**Check:** ruff clean, mypy clean on 13 files, all tests pass (a handful skip
without GMS, which is fine — they detect it themselves).

---

## 6. Run the demo end to end

```bash
chmod +x demo.sh          # the bridge doesn't preserve the exec bit
./demo.sh
```

This scans, emits, guards, sandboxes, and drifts. **Check:** it reaches
`==> Done` and prints the four links.

Along the way you should see:

- `resolved 8/9 data references` — the 9th is `analytics.events`, genuinely
  absent from the showcase data
- `read via: aspects - 14 AgentLens node(s) examined` in the guard step
- `2 break, 1 degrade, 4 unaffected` in the sandbox step
- `exit code 1 - non-zero, so this fails a CI check` in the drift step

---

## 7. Put the explorer in DataHub's UI

```bash
python -m agentlens.cli serve --link
```

**Check:** it prints `linked` for three tables, then
`AgentLens explorer on http://localhost:8000`. Leave it running.

Now open **http://localhost:9002** (login `datahub` / `datahub`), search for
`order_details`, open it, and look at the right sidebar under **Links**:

> Simulate a change to this table (AgentLens)

Click it. The explorer opens with that table already selected.

---

## 8. The Agents tab, inside DataHub's own UI

Section 7 gets you a sidebar link to a separate page. This gets you a real
entity tab, sitting next to **Lineage**, in DataHub's own components.

It works without rebuilding a Docker image or touching gradle: DataHub's React
app is a Vite dev server that proxies to the stack you already have running on
`:9002`. You run the UI in front of it.

First time only — install and generate. Both are slow and both are one-shot:

```bash
cd ~/Desktop/DataHub/datahub/datahub-web-react
npx yarn@1.22.22 install --frozen-lockfile      # ~45s
node scripts/generate-lazy-icon-stubs.js        # 1512 icon stubs
npx yarn@1.22.22 run generate                   # graphql codegen -> types.generated.ts
```

Then, every time — three processes:

```bash
# 1. the stack (section 1)
datahub docker quickstart --version v1.6.0

# 2. the fleet payload, from the agentlens repo
python -m agentlens.cli serve --port 8000

# 3. the UI, from datahub-web-react
npx yarn@1.22.22 vite
```

### Keep the images and the checkout on the same commit

The React app is served from source by vite, but its GraphQL documents are
generated from `datahub-graphql-core`'s schema in *this checkout*. GMS validates
every query against the schema in *the image*. If the two drift, GMS rejects the
queries with `FieldUndefined`, `data` comes back null, and the app renders
skeletons forever — `NavSidebar.tsx` gates on `!appConfig.loaded || !me.loaded`,
so the whole left nav is a pile of grey bars and nothing else loads either.

That is not a subtle failure mode and it is not obviously a version problem when
you hit it. Running this branch against the older `v1.6.0` images broke **128 of
316 operations** off **38 missing fields** — one of them,
`DataPlatformProperties.logical`, appears in a shared platform fragment used by
195 selections.

To check after any rebase or image change:

```bash
cd ~/Desktop/DataHub/datahub && git log -1 --format=%h    # must match the image tag
```

The branch is `agentlens-demo` at `ee7a980`; the images are `sha-ee7a980`. If you
move the branch, find a published image for the new commit
(`https://hub.docker.com/v2/repositories/acryldata/datahub-gms/tags/sha-<short>`),
update `DATAHUB_VERSION` in `~/.datahub/quickstart/agentlens-stack.env`, refetch
the compose file from that commit, and re-run `yarn generate`. Not every commit
gets an image — pick one that does and rebase onto it.

There is no proxy workaround for this. Routing GraphQL around the frontend
straight to GMS fixes the transport but not the schema, and it costs you real
auth: the unauthenticated actor resolves as `__datahub_system`, not the
`datahub` user you logged in as.

Open **http://localhost:3000** — not 9002 — and log in as `datahub` /
`datahub`. Search for `order_details`, open it, and there is an **Agents** tab
with a robot icon next to Lineage.

**Check:** the tab is *absent* on a table no agent reads. That is the point of
it — `display.visible` is keyed off the `has-agent-consumers` tag that
`agentlens guard` writes, so the tab appears on exactly the tables the fleet
reads and nowhere else. If you have not run `guard` yet, no tab will appear
anywhere.

**Check:** `curl localhost:3000/agentlens/payload.json` returns JSON. That is
the Vite proxy forwarding to `agentlens serve` on `:8000`, which is how the tab
gets its data same-origin. Override the target with
`REACT_APP_AGENTLENS_TARGET` if you moved the port.

### What was changed in the DataHub checkout

Four new files under
`datahub-web-react/src/app/entityV2/dataset/profile/AgentImpact/`, plus three
edits:

| File | Edit |
|---|---|
| `dataset/DatasetEntity.tsx` | the `Agents` tab entry in `getProfileTabs()` |
| `vite.config.ts` | the `/agentlens` proxy entry |
| `src/app/useSetAppTheme.tsx` | **an upstream bug fix, unrelated to us** |

That last one blocks `vite` from starting at all. `useSetAppTheme.tsx` lives in
`src/app/`, and dynamically imports `` `./conf/theme/${id}` `` — but the themes
are at `src/conf/theme/`, so from that module the path needs `../`. esbuild's
dependency scan cannot resolve the glob and fails the boot with *"Could not
resolve import('./conf/theme/**/*')"*. One character. Worth filing.

---

## If something goes sideways

**`permission denied: ./demo.sh`** — `chmod +x demo.sh`. Writing files through
the bridge drops the exec bit. Once you `git add` it with the bit set, git
keeps it.

**`Could not connect to DataHub server`** from any `datahub` CLI command —
check `~/.datahubenv`. The `server:` line must be `http://localhost:8080`, not
`datahub`.

**`link` prints `skipped` with "could not read the existing links"** — GMS
isn't reachable. It refuses to write rather than risk deleting links that are
already on those tables. Fix step 1 and re-run.

**Port 8000 in use** — `python -m agentlens.cli serve --port 8123 --link`. The
link is rewritten in place, so you won't accumulate one link per port.

**Everything looks stale after editing a skill** — the explorer rebuilds on
every request, so just refresh. `manifest.json` only updates when you re-run
`scan`, so re-run `./demo.sh` if you changed which tables a skill reads.

---

## Full reset (only if you must)

Destroys all DataHub data:

```bash
datahub docker nuke
docker compose --profile quickstart \
  -f ~/.datahub/quickstart/docker-compose.ee7a980.yml \
  --env-file ~/.datahub/quickstart/agentlens-stack.env \
  -p datahub up -d
export DATAHUB_GMS_URL=http://localhost:8080 DATAHUB_GMS_TOKEN=dummy
datahub datapack load showcase-ecommerce
./demo.sh
python -m agentlens.cli serve --link
```

`~/.datahubenv` survives a nuke, so `datahub init` is only needed if that file is
missing or points somewhere other than `http://localhost:8080`.
