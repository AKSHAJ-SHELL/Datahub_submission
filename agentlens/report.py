"""Render a blast radius report as a self-contained HTML page.

One file, no assets, no network required. Opens anywhere. This is what goes
in examples/ so a judge can see the output without standing up the stack.

Layout follows the thing it describes: the root asset sits at the left, and
impact propagates rightward one column per hop. Distance is the structure.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone

CSS = """
:root {
  --ground:   #EDF0F4;
  --panel:    #FFFFFF;
  --ink:      #16202E;
  --ink-soft: #5A6878;
  --rule:     #D3DAE3;
  --alert:    #C2701C;
  --alert-bg: #FBF0E2;
  --calm:     #2F7A6F;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0;
  background: var(--ground);
  color: var(--ink);
  font-family: 'IBM Plex Sans', ui-sans-serif, system-ui, -apple-system, sans-serif;
  font-size: 15px; line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1080px; margin: 0 auto; padding: 48px 32px 80px; }

.eyebrow {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 11px; letter-spacing: .16em; text-transform: uppercase;
  color: var(--ink-soft); margin: 0 0 10px;
}
h1 {
  font-family: 'Space Grotesk', 'IBM Plex Sans', sans-serif;
  font-size: 38px; font-weight: 600; letter-spacing: -.02em;
  margin: 0 0 28px; line-height: 1.1;
}

.change {
  background: var(--panel); border: 1px solid var(--rule);
  border-left: 3px solid var(--alert);
  padding: 18px 22px; margin-bottom: 34px;
}
.change dt {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 10px; letter-spacing: .14em; text-transform: uppercase;
  color: var(--ink-soft); margin-bottom: 3px;
}
.change dd { margin: 0 0 14px; }
.change dd:last-child { margin-bottom: 0; }
.urn {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 12px; word-break: break-all; color: var(--ink);
}

.verdict {
  display: flex; align-items: baseline; gap: 14px;
  padding: 26px 0 22px; border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule); margin-bottom: 40px;
}
.verdict .n {
  font-family: 'Space Grotesk', sans-serif;
  font-size: 68px; font-weight: 600; line-height: 1;
  letter-spacing: -.03em; color: var(--alert);
}
.verdict.safe .n { color: var(--calm); }
.verdict .label { font-size: 17px; color: var(--ink-soft); }
.verdict .label strong { color: var(--ink); font-weight: 600; }

h2 {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 11px; letter-spacing: .16em; text-transform: uppercase;
  color: var(--ink-soft); font-weight: 500;
  margin: 0 0 16px; padding-bottom: 8px; border-bottom: 1px solid var(--rule);
}

.cascade { display: flex; gap: 0; align-items: stretch; margin-bottom: 44px; overflow-x: auto; }
.hop { min-width: 210px; flex: 1; padding: 0 18px; border-left: 1px solid var(--rule); }
.hop:first-child { padding-left: 0; border-left: none; }
.hop-n {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 10px; letter-spacing: .14em; text-transform: uppercase;
  color: var(--ink-soft); margin-bottom: 12px;
}
.node {
  background: var(--panel); border: 1px solid var(--rule);
  padding: 11px 13px; margin-bottom: 8px;
}
.node.root { border-color: var(--ink); border-width: 1.5px; }
.node.agent { background: var(--alert-bg); border-color: #E5C89C; }
.node .nm { font-weight: 600; font-size: 14px; word-break: break-word; }
.node .kd {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 10px; letter-spacing: .1em; text-transform: uppercase;
  color: var(--ink-soft); margin-top: 2px;
}

table { width: 100%; border-collapse: collapse; margin-bottom: 40px; }
th {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 10px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--ink-soft); font-weight: 500;
  text-align: left; padding: 0 14px 9px 0; border-bottom: 1px solid var(--rule);
}
td { padding: 12px 14px 12px 0; border-bottom: 1px solid var(--rule); vertical-align: top; }
td.name { font-weight: 600; }
td.path {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 11.5px; color: var(--ink-soft); word-break: break-all;
}
.pill {
  display: inline-block; padding: 2px 8px;
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 10px; letter-spacing: .08em; text-transform: uppercase;
  background: var(--ground); border: 1px solid var(--rule);
}

.wrote { background: var(--panel); border: 1px solid var(--rule); padding: 4px 20px; }
.wrote li {
  list-style: none; padding: 11px 0; border-bottom: 1px solid var(--rule);
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 12.5px;
}
.wrote li:last-child { border-bottom: none; }
.wrote ul { margin: 0; padding: 0; }

footer {
  margin-top: 56px; padding-top: 18px; border-top: 1px solid var(--rule);
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 11px; color: var(--ink-soft); display: flex;
  justify-content: space-between; flex-wrap: wrap; gap: 10px;
}
.empty { color: var(--ink-soft); font-style: italic; padding: 8px 0 28px; }

@media (max-width: 720px) {
  .wrap { padding: 32px 20px 60px; }
  h1 { font-size: 28px; }
  .verdict .n { font-size: 52px; }
  .cascade { flex-direction: column; }
  .hop { border-left: none; border-top: 1px solid var(--rule); padding: 16px 0 0; }
  .hop:first-child { border-top: none; padding-top: 0; }
}
"""

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=IBM+Plex+Mono:wght@400;500&'
    'family=IBM+Plex+Sans:wght@400;600&'
    'family=Space+Grotesk:wght@500;600&display=swap" rel="stylesheet">'
)


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _short(urn: str) -> str:
    """Pull the readable name out of a dataset URN."""
    if "," in urn:
        parts = urn.split(",")
        if len(parts) >= 2:
            return parts[1]
    return urn


def _cascade(report: dict) -> str:
    items = report["agents"] + report["skills"] + report["tools"]
    max_hop = max([i["hops"] for i in items], default=0)

    cols = ['<div class="hop"><div class="hop-n">source</div>'
            f'<div class="node root"><div class="nm">{_e(_short(report["root"]))}</div>'
            '<div class="kd">changed asset</div></div></div>']

    for hop in range(1, max_hop + 1):
        at_hop = [i for i in items if i["hops"] == hop]
        if not at_hop:
            continue
        nodes = []
        for item in sorted(at_hop, key=lambda x: x["kind"]):
            cls = "node agent" if item["kind"] == "agent" else "node"
            nodes.append(
                f'<div class="{cls}"><div class="nm">{_e(item["name"])}</div>'
                f'<div class="kd">{_e(item["kind"])}</div></div>'
            )
        cols.append(
            f'<div class="hop"><div class="hop-n">{hop} hop{"s" if hop > 1 else ""}</div>'
            + "".join(nodes) + "</div>"
        )

    return '<div class="cascade">' + "".join(cols) + "</div>"


def _table(title: str, items: list[dict]) -> str:
    if not items:
        return f"<h2>{_e(title)}</h2><p class='empty'>None.</p>"

    rows = []
    for item in sorted(items, key=lambda x: (x["hops"], x["name"])):
        team = item.get("owner_team") or "&mdash;"
        path = item.get("source_path") or ""
        repo = item.get("repository") or ""
        loc = f"{repo}/{path}" if path else "&mdash;"
        rows.append(
            "<tr>"
            f'<td class="name">{_e(item["name"])}</td>'
            f'<td><span class="pill">{_e(item["hops"])} '
            f'hop{"s" if item["hops"] != 1 else ""}</span></td>'
            f"<td>{_e(team) if team != '&mdash;' else team}</td>"
            f'<td class="path">{_e(loc) if loc != "&mdash;" else loc}</td>'
            "</tr>"
        )

    return (
        f"<h2>{_e(title)}</h2><table>"
        "<thead><tr><th>Name</th><th>Distance</th><th>Owner</th><th>Source</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


def render_html(report: dict, reason: str = "", actions: list[str] | None = None) -> str:
    agents = report["agents"]
    n = len(agents)
    safe = "" if n else " safe"

    if n:
        label = (
            f"<strong>{n} agent{'s' if n != 1 else ''}</strong> "
            f"lose context if this change ships. "
            f"{len(report['skills'])} skill{'s' if len(report['skills']) != 1 else ''} affected."
        )
    else:
        label = "No agents read this asset. The change is clear."

    wrote = ""
    if actions:
        entries = "".join(f"<li>{_e(a.strip())}</li>" for a in actions if a.strip())
        wrote = f"<h2>Written back to DataHub</h2><div class='wrote'><ul>{entries}</ul></div>"

    stamp = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentLens &mdash; blast radius</title>
{FONTS}
<style>{CSS}</style>
</head><body><div class="wrap">

<p class="eyebrow">AgentLens &mdash; agent blast radius</p>
<h1>Which agents break<br>if this changes?</h1>

<dl class="change">
  <dt>Proposed change</dt>
  <dd>{_e(reason) or "Unspecified"}</dd>
  <dt>Asset</dt>
  <dd class="urn">{_e(report["root"])}</dd>
</dl>

<div class="verdict{safe}">
  <div class="n">{n}</div>
  <div class="label">{label}</div>
</div>

<h2>Propagation</h2>
{_cascade(report)}

{_table("Agents", agents)}
{_table("Skills", report["skills"])}
{_table("Tools", report["tools"])}

{wrote}

<footer>
  <span>Generated {stamp}</span>
  <span>{report.get("total_downstream", 0)} total downstream assets traversed</span>
</footer>

</div></body></html>"""


def write_html(report: dict, path: str, reason: str = "",
               actions: list[str] | None = None) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_html(report, reason, actions))
    return path
