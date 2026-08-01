#!/usr/bin/env bash
#
# AgentLens end-to-end demo.
#
#   ./demo.sh
#
# Assumes DataHub is running. If it isn't, or you want a genuinely clean
# slate, see RESET below.
#
# ---------------------------------------------------------------------------
# RESET (destroys all DataHub data, then rebuilds):
#
#   datahub docker nuke
#   datahub docker quickstart --version v1.6.0
#   datahub init                                  # host: press Enter for
#                                                 #   http://localhost:8080
#                                                 # token: press Enter (blank)
#   # NB: the datahub/datahub in the quickstart banner is the *frontend*
#   # login at :9002. It is not the answer to either init prompt.
#   datahub datapack load showcase-ecommerce
#   ./demo.sh
#
# ---------------------------------------------------------------------------

set -euo pipefail

GMS="${DATAHUB_GMS_URL:-http://localhost:8080}"
FRONTEND="${DATAHUB_FRONTEND_URL:-http://localhost:9002}"
export DATAHUB_GMS_URL="$GMS"
export DATAHUB_GMS_TOKEN="${DATAHUB_GMS_TOKEN:-dummy}"

REPO="github.com/acme/data-agents"

say()  { printf "\n\033[1m==> %s\033[0m\n" "$1"; }
fail() { printf "\n\033[31m!! %s\033[0m\n" "$1"; exit 1; }

# ---------------------------------------------------------------------------
say "Checking DataHub"

if ! curl -sf "$GMS/config" > /dev/null; then
  fail "DataHub is not reachable at $GMS.
   Start it with:  datahub docker quickstart --version v1.6.0"
fi

VERSION=$(curl -s "$GMS/config" | python3 -c \
  'import json,sys; print(json.load(sys.stdin).get("versions",{}).get("acryldata/datahub",{}).get("version","?"))')
echo "    GMS $VERSION"

# ---------------------------------------------------------------------------
say "Checking the catalog has data"

DATASETS=$(curl -s -X POST "$GMS/api/graphql" \
  -H 'Content-Type: application/json' \
  -d '{"query":"query($i: SearchAcrossEntitiesInput!){searchAcrossEntities(input:$i){total}}",
       "variables":{"i":{"types":["DATASET"],"query":"*","start":0,"count":1}}}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["searchAcrossEntities"]["total"])')

echo "    $DATASETS datasets in the catalog"

if [ "$DATASETS" -lt 10 ]; then
  fail "The catalog looks empty. Load the sample data first:
   datahub init      # accept the default host, leave the token blank
   datahub datapack load showcase-ecommerce

   If init says it can't connect, check ~/.datahubenv - server: must be
   $GMS, not 'datahub'."
fi

# ---------------------------------------------------------------------------
say "Scanning the agent repository"
python3 -m agentlens.cli scan demo-repo --repository "$REPO"

# ---------------------------------------------------------------------------
say "Writing the fleet into DataHub"
python3 -m agentlens.cli emit manifest.json

# ---------------------------------------------------------------------------
say "Picking the most depended-on table"

# Whichever real table the most skills resolved to. Discovered rather than
# hardcoded, because datapack URNs change between loads.
TARGET=$(python3 - <<'PY'
import json, collections
m = json.load(open("manifest.json"))
counts = collections.Counter(
    ref["resolved_urn"]
    for skill in m["skills"]
    for ref in skill["data_refs"]
    if ref.get("resolved_urn")
)
if not counts:
    raise SystemExit("NONE")
print(counts.most_common(1)[0][0])
PY
)

[ "$TARGET" = "NONE" ] && fail "No data references resolved. Is the showcase datapack loaded?"
echo "    $TARGET"

# ---------------------------------------------------------------------------
say "Guarding a schema change"

mkdir -p examples
python3 -m agentlens.cli guard "$TARGET" \
  --reason "dropping column discount_pct" \
  --html examples/blast-radius.html \
  --json examples/guard-run.json

# ---------------------------------------------------------------------------
say "Simulating the change instead of guessing at it"

# guard just said every downstream node degrades, which is what the lineage
# graph supports. But `line_total` is a column, and only some of those skills
# name it. Nothing in DataHub can answer that - impact analysis is always
# about the graph as it is, never about a change that hasn't happened.

set +e
python3 -m agentlens.cli sandbox order_details \
  --drop-column line_total --repo demo-repo | sed 's/^/    /'
SANDBOX_RC=${PIPESTATUS[0]}
set -e
echo "    exit code $SANDBOX_RC - nothing was written to DataHub"

# ---------------------------------------------------------------------------
say "Catching the catalog going stale"

# The catalog was written seconds ago, so it agrees with the repo. Then we
# change what a skill reads - a one-line edit to a markdown file, nothing
# touching DataHub - and ask again. That gap is invisible to everything else.

SKILL="demo-repo/skills/revenue-lookup/SKILL.md"
SKILL_BACKUP="$(mktemp)"
cp "$SKILL" "$SKILL_BACKUP"
restore_skill() { cp "$SKILL_BACKUP" "$SKILL"; rm -f "$SKILL_BACKUP"; }
trap restore_skill EXIT

echo "    the catalog was written seconds ago, so the only thing outstanding"
echo "    should be the reference that never resolved:"
python3 -m agentlens.cli drift demo-repo --repository "$REPO" --exit-zero \
  | sed 's/^/    /'

echo
echo "    now repointing revenue-lookup from analytics.order_details to"
echo "    analytics.orders - one line of markdown, nothing touches DataHub."
echo "    (the showcase keeps orders in the order_entry schema, so the"
echo "     resolver leaf-matches it there - which is why the REF + below"
echo "     reads order_entry.orders)"
python3 - "$SKILL" <<'PY'
import sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    text = fh.read()
with open(path, "w", encoding="utf-8") as fh:
    fh.write(text.replace("analytics.order_details", "analytics.orders"))
PY
echo

set +e
python3 -m agentlens.cli drift demo-repo --repository "$REPO" | sed 's/^/    /'
DRIFT_RC=${PIPESTATUS[0]}
set -e
echo "    exit code $DRIFT_RC - non-zero, so this fails a CI check"

# The trap puts the skill back, so ./demo.sh is idempotent.

# ---------------------------------------------------------------------------
say "Done"

cat <<EOF

  Impact report:
    open examples/blast-radius.html

  The table you just guarded, now tagged \`has-agent-consumers\`:
    $FRONTEND/dataset/$TARGET

  Its lineage - skills at one hop, agents at two:
    $FRONTEND/dataset/$TARGET/Lineage

  The whole fleet:
    $FRONTEND/browse/dataset/prod/agentlens

  The drift check above is the same command CI would run:
    agentlens drift <repo> --repository <name>     # exits 1 on drift

EOF
