"""Build a self-contained, clickable view of the sandbox.

Everything the page needs is embedded at build time: the downstream index, the
node metadata, the skill text, and the agent-to-skill map - exactly the
:class:`agentlens.sandbox.Fleet` the CLI simulates against. The browser then
runs the same simulation locally, which is why the page works with no server
and no network.

The JS simulation mirrors :func:`agentlens.sandbox.simulate`. If you change the
severity rules there, change them in ``_SIMULATE_JS`` too - they are marked in
both files.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from .model import Manifest
from .sandbox import Fleet, fork

# Columns are discovered as `alias.column` in the skill text, which is what the
# demo repo and most SQL-bearing skills look like. Free text covers the rest.
_QUALIFIED = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{0,3})\.([a-z_][a-z0-9_]{2,})\b")


def discover_columns(fleet: Fleet) -> list[str]:
    """Column names worth offering as one-click options."""
    found: set[str] = set()
    for text in fleet.skill_text.values():
        for _alias, column in _QUALIFIED.findall(text):
            found.add(column)
    return sorted(found)


def build_payload(manifest: Manifest, repo_root: str) -> dict[str, Any]:
    fleet = fork(manifest, repo_root)

    tables: dict[str, dict[str, Any]] = {}
    for skill in manifest.skills:
        for ref in skill.data_refs:
            if not ref.resolved_urn:
                continue
            entry = tables.setdefault(ref.resolved_urn, {"raw": ref.raw, "readers": []})
            if skill.id not in entry["readers"]:
                entry["readers"].append(skill.id)

    return {
        "index": fleet.index,
        "meta": fleet.meta,
        "skillText": fleet.skill_text,
        "agentSkills": fleet.agent_skills,
        "tables": tables,
        "columns": discover_columns(fleet),
        "unresolved": sorted({
            r.raw for s in manifest.skills for r in s.data_refs if not r.resolved_urn
        }),
        "counts": {
            "agents": len(manifest.agents),
            "skills": len(manifest.skills),
            "tools": len(manifest.tools),
        },
        "repository": manifest.repository,
    }


def render_html(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, indent=None, separators=(",", ":"))
    # </script> inside embedded JSON would close the tag early.
    blob = blob.replace("</", "<\\/")
    return _PAGE.replace("__PAYLOAD__", blob).replace(
        "__REPO__", html.escape(payload.get("repository") or "your agent repos")
    )


def write(manifest: Manifest, repo_root: str, out_path: str) -> dict[str, Any]:
    payload = build_payload(manifest, repo_root)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(render_html(payload))
    return payload


# ---------------------------------------------------------------------------

_SIMULATE_JS = r"""
// Mirrors agentlens/sandbox.py :: simulate(). Keep the two in step.
function simulate(change) {
  const reached = new Map();
  let frontier = [change.table];
  const seen = new Set(frontier);
  for (let hop = 1; hop <= 6; hop++) {
    const next = [];
    for (const urn of frontier)
      for (const child of (D.index[urn] || []))
        if (!seen.has(child)) { seen.add(child); reached.set(child, hop); next.push(child); }
    frontier = next;
    if (!frontier.length) break;
  }

  const effects = [], broken = new Set();
  for (const [urn, hops] of reached) {
    const node = D.meta[urn] || {};
    if (node.kind !== "skill") continue;
    const text = D.skillText[node.id] || "";
    let severity, why;
    if (change.kind === "drop-column") {
      if (!text) { severity = "breaks"; why = "could not read the skill file - assuming affected"; }
      else if (mentions(text, change.column)) { severity = "breaks"; why = `names ${change.column} in its text`; }
      else { severity = "unchanged"; why = `reads the table but never names ${change.column}`; }
    } else if (change.kind === "rename-table") {
      severity = "breaks"; why = `names the old table; text must change to ${change.newName || "the new name"}`;
    } else {
      severity = "breaks"; why = "the table it reads would not exist";
    }
    if (severity === "breaks") broken.add(node.id);
    effects.push({severity, kind: "skill", name: node.name || node.id, id: node.id, hops, why,
                  sourcePath: node.source_path || "", ownerTeam: ""});
  }

  for (const [urn, hops] of reached) {
    const node = D.meta[urn] || {};
    if (node.kind !== "agent") continue;
    const via = (D.agentSkills[node.id] || []).filter(s => broken.has(s)).sort();
    effects.push({
      severity: via.length ? "degrades" : "unchanged", kind: "agent",
      name: node.name || node.id, id: node.id, hops,
      why: via.length ? "via " + via.join(", ") : "none of its skills name the change",
      sourcePath: node.source_path || "", ownerTeam: node.owner_team || "",
    });
  }

  const rank = {breaks: 0, degrades: 1, unchanged: 2};
  effects.sort((a, b) => rank[a.severity] - rank[b.severity] || a.kind.localeCompare(b.kind)
                        || a.name.localeCompare(b.name));
  return effects;
}

// Word-boundary, case-insensitive: `discount_pct` matches `o.discount_pct`
// and `SUM(discount_pct)` but not `discount_pct_v2`.
function mentions(text, column) {
  if (!text || !column) return false;
  return new RegExp(`\\b${column.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(text);
}
"""

_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentLens Explorer</title>
<style>
:root{--bg:#fff;--surface:#f6f7f9;--surface2:#eef1f5;--ink:#16181d;--muted:#6b7280;
--border:#d8dce2;--accent:#2563eb;--accent-soft:#dbe6fe;--data:#0d9488;--data-soft:#d3f0ec;
--warn:#b45309;--warn-soft:#fdecd2;--danger:#c8321f;--danger-soft:#fbdfda;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media(prefers-color-scheme:dark){:root{--bg:#131417;--surface:#1b1d22;--surface2:#22252b;
--ink:#e7e9ee;--muted:#9aa1ad;--border:#31353d;--accent:#7ba4f8;--accent-soft:#1e2a44;
--data:#5ec9bd;--data-soft:#123330;--warn:#e3a951;--warn-soft:#3a2e17;
--danger:#ef7f6d;--danger-soft:#3d1e19}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Inter,sans-serif}
.wrap{max-width:1140px;margin:0 auto;padding:34px 22px 80px}
h1{font-size:1.5rem;margin:0 0 4px;letter-spacing:-.02em}
.sub{color:var(--muted);margin:0 0 4px;font-size:.95rem}
.sub code{font-family:var(--mono);font-size:.85em}
.grid{display:grid;grid-template-columns:288px 1fr;gap:22px;margin-top:26px}
@media(max-width:840px){.grid{grid-template-columns:1fr}}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:11px;padding:16px 18px}
.lbl{font-size:.7rem;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);font-weight:650;margin:0 0 9px}
.tbl{display:block;width:100%;text-align:left;background:none;border:1px solid transparent;
border-radius:7px;padding:8px 10px;cursor:pointer;color:var(--ink);font:inherit;margin-bottom:3px}
.tbl:hover{background:var(--surface2)}
.tbl[aria-selected=true]{background:var(--data-soft);border-color:var(--data)}
.tbl .n{font-family:var(--mono);font-size:.82rem;display:block;word-break:break-all}
.tbl .m{color:var(--muted);font-size:.75rem}
.seg{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.seg button{background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:6px 12px;
cursor:pointer;color:var(--ink);font:inherit;font-size:.87rem}
.seg button[aria-pressed=true]{background:var(--accent-soft);border-color:var(--accent);font-weight:600}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px}
.chip{background:var(--bg);border:1px solid var(--border);border-radius:99px;padding:4px 11px;
cursor:pointer;font-family:var(--mono);font-size:.78rem;color:var(--ink)}
.chip:hover{border-color:var(--accent)}
.chip[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}
input[type=text]{width:100%;padding:8px 11px;border:1px solid var(--border);border-radius:7px;
background:var(--bg);color:var(--ink);font-family:var(--mono);font-size:.85rem}
.counts{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 4px}
.count{border-radius:8px;padding:8px 13px;border:1px solid var(--border);background:var(--bg);min-width:96px}
.count b{display:block;font-size:1.32rem;line-height:1.15;font-variant-numeric:tabular-nums}
.count span{font-size:.72rem;text-transform:uppercase;letter-spacing:.075em;color:var(--muted)}
.count.breaks{background:var(--danger-soft);border-color:var(--danger)}
.count.degrades{background:var(--warn-soft);border-color:var(--warn)}
.row{display:flex;gap:12px;align-items:baseline;padding:9px 12px;border-radius:8px;margin-bottom:4px;
border:1px solid var(--border);background:var(--bg)}
.row.breaks{background:var(--danger-soft);border-color:var(--danger)}
.row.degrades{background:var(--warn-soft);border-color:var(--warn)}
.tagx{font-family:var(--mono);font-size:.68rem;font-weight:700;letter-spacing:.05em;
padding:2px 7px;border-radius:5px;background:var(--surface2);white-space:nowrap}
.row.breaks .tagx{background:var(--danger);color:#fff}
.row.degrades .tagx{background:var(--warn);color:#fff}
.nm{font-weight:640;min-width:150px}
.why{color:var(--muted);font-size:.88rem;flex:1}
.team{font-family:var(--mono);font-size:.74rem;color:var(--muted);white-space:nowrap}
.cmp{margin:18px 0 0;padding:13px 16px;border-radius:9px;background:var(--accent-soft);
border:1px solid var(--accent);font-size:.92rem}
.cmp b{font-variant-numeric:tabular-nums}
.cli{margin-top:16px;padding:11px 14px;background:var(--surface2);border:1px solid var(--border);
border-radius:8px;font-family:var(--mono);font-size:.78rem;overflow-x:auto;white-space:pre}
footer{margin-top:34px;color:var(--muted);font-size:.83rem;border-top:1px solid var(--border);padding-top:16px}
</style></head><body><div class="wrap">

<h1>AgentLens Explorer</h1>
<p class="sub">A change that hasn't happened, applied to a fork of the graph. Nothing here writes to DataHub.</p>
<p class="sub">Scanned from <code>__REPO__</code> &middot; <span id="fleet"></span></p>

<div class="grid">
  <div class="panel">
    <p class="lbl">Table</p>
    <div id="tables"></div>
    <p class="lbl" style="margin-top:18px">Not in the catalog</p>
    <div id="unresolved" class="sub" style="font-family:var(--mono);font-size:.78rem"></div>
  </div>

  <div>
    <div class="panel">
      <p class="lbl">Proposed change</p>
      <div class="seg" id="kinds">
        <button data-kind="drop-column" aria-pressed="true">Drop a column</button>
        <button data-kind="rename-table" aria-pressed="false">Rename the table</button>
        <button data-kind="drop-table" aria-pressed="false">Drop the table</button>
      </div>
      <div id="colwrap">
        <p class="lbl">Column &mdash; click one, or type any</p>
        <div class="chips" id="chips"></div>
        <input type="text" id="col" placeholder="column name" autocomplete="off">
      </div>
      <div id="renamewrap" hidden>
        <p class="lbl">New name</p>
        <input type="text" id="newname" value="analytics.orders_v2" autocomplete="off">
      </div>
    </div>

    <div class="counts" id="counts"></div>
    <div id="effects"></div>
    <div class="cmp" id="cmp"></div>
    <div class="cli" id="cli"></div>
  </div>
</div>

<footer>
  The browser runs the same simulation as <code>agentlens sandbox</code>, over data embedded when
  this page was built. Re-run <code>agentlens explore</code> after changing your skills.
  The CLI is authoritative.
</footer>

</div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const D = JSON.parse(document.getElementById("payload").textContent);
__SIMULATE__

// Deep-linkable, so DataHub can point straight at a table: ?table=<urn>&column=<name>
const Q = new URLSearchParams(location.search);
const TK = Object.keys(D.tables);
const want = Q.get("table");
const picked = want ? (D.tables[want] ? want : TK.find(u => u.includes(want))) : null;
const S = {table: picked || TK[0] || "", kind: Q.get("kind") || "drop-column",
           column: Q.get("column") || "", newName: Q.get("newName") || "analytics.orders_v2"};
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const short = u => u && u.includes(",") ? u.split(",")[1] : u;

$("fleet").textContent = `${D.counts.agents} agents, ${D.counts.skills} skills, ${D.counts.tools} tools`;

$("tables").innerHTML = Object.entries(D.tables).map(([urn, t]) =>
  `<button class="tbl" data-urn="${esc(urn)}" aria-selected="${urn === S.table}">
     <span class="n">${esc(short(urn))}</span>
     <span class="m">${t.readers.length} skill${t.readers.length === 1 ? "" : "s"} read it</span>
   </button>`).join("");

$("unresolved").innerHTML = D.unresolved.length
  ? D.unresolved.map(esc).join("<br>") : "&mdash;";

$("chips").innerHTML = D.columns.map(c =>
  `<button class="chip" data-col="${esc(c)}" aria-pressed="false">${esc(c)}</button>`).join("");

$("tables").onclick = e => { const b = e.target.closest(".tbl"); if (!b) return;
  S.table = b.dataset.urn;
  [...$("tables").children].forEach(x => x.setAttribute("aria-selected", x === b));
  render(); };

$("kinds").onclick = e => { const b = e.target.closest("button"); if (!b) return;
  S.kind = b.dataset.kind;
  [...$("kinds").children].forEach(x => x.setAttribute("aria-pressed", x === b));
  $("colwrap").hidden = S.kind !== "drop-column";
  $("renamewrap").hidden = S.kind !== "rename-table";
  render(); };

$("chips").onclick = e => { const b = e.target.closest(".chip"); if (!b) return;
  S.column = b.dataset.col; $("col").value = S.column;
  [...$("chips").children].forEach(x => x.setAttribute("aria-pressed", x === b));
  render(); };

if (S.column) {
  $("col").value = S.column;
  [...$("chips").children].forEach(x => x.setAttribute("aria-pressed", x.dataset.col === S.column));
}
if (S.kind !== "drop-column") {
  [...$("kinds").children].forEach(x => x.setAttribute("aria-pressed", x.dataset.kind === S.kind));
  $("colwrap").hidden = true;
  $("renamewrap").hidden = S.kind !== "rename-table";
}

$("col").oninput = e => { S.column = e.target.value.trim();
  [...$("chips").children].forEach(x => x.setAttribute("aria-pressed", x.dataset.col === S.column));
  render(); };
$("newname").oninput = e => { S.newName = e.target.value.trim(); render(); };

function render() {
  if (!S.table) return;
  const effects = (S.kind === "drop-column" && !S.column) ? [] : simulate(S);
  const n = s => effects.filter(e => e.severity === s).length;

  $("counts").innerHTML = [["breaks","break"],["degrades","degrade"],["unchanged","unaffected"]]
    .map(([k, label]) => `<div class="count ${k}"><b>${n(k)}</b><span>${label}</span></div>`).join("");

  $("effects").innerHTML = effects.length ? effects.map(e => `
    <div class="row ${e.severity}">
      <span class="tagx">${e.severity.toUpperCase()}</span>
      <span class="nm">${esc(e.name)}</span>
      <span class="why">${esc(e.why)}</span>
      <span class="team">${esc(e.ownerTeam || e.kind)}</span>
    </div>`).join("")
    : `<div class="row"><span class="why">${S.kind === "drop-column" && !S.column
        ? "Pick a column, or type one." : "Nothing downstream reads this table."}</span></div>`;

  const downstream = effects.length;
  const flagged = n("breaks") + n("degrades");
  $("cmp").innerHTML = !effects.length ? "&nbsp;" : (S.kind === "drop-column"
    ? `A lineage-only tool flags every consumer of this table: <b>${downstream}</b>.
       AgentLens says <b>${flagged}</b>, because only some name the column.
       ${downstream - flagged > 0 ? `<b>${downstream - flagged}</b> would have been chased for nothing.` : ""}`
    : `Every consumer breaks &mdash; the table itself is going away, so naming the column doesn't help. <b>${downstream}</b> affected.`);

  const flag = S.kind === "drop-column" ? `--drop-column ${S.column || "<column>"}`
    : S.kind === "rename-table" ? `--rename-to ${S.newName || "<name>"}` : "--drop-table";
  $("cli").textContent = `agentlens sandbox ${short(S.table)} ${flag}`;
}
render();
</script></body></html>
"""

_PAGE = _PAGE.replace("__SIMULATE__", _SIMULATE_JS)
