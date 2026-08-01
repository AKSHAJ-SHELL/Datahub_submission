#!/usr/bin/env python3
"""
Adds `agentlens serve` and `agentlens link` - the explorer, reachable from
DataHub at localhost:9002.

Run from inside agentlens-project/ (after add_explorer.py, and re-run
add_explorer.py first so the page understands deep links):

    python add_explorer.py
    python add_serve.py
    ruff check --fix . && ruff check . && mypy agentlens && pytest -q

    python -m agentlens.cli serve            # http://localhost:8000
    python -m agentlens.cli link             # puts it in DataHub's UI

How it reaches the DataHub UI
-----------------------------
DataHub's frontend is a React app; embedding a page inside it means forking it,
which is the one thing this project has avoided throughout. So instead:

  * `serve` runs the explorer on localhost over the standard library. It
    rebuilds the page on every request, so editing a SKILL.md and refreshing
    shows the new answer.

  * `link` writes an `institutionalMemory` element onto each catalogued table -
    a first-class DataHub aspect that the UI renders as **Links** in the entity
    sidebar. Open `analytics.order_details` at localhost:9002 and there is a
    "Simulate a change to this table" link that deep-links the explorer with
    that table already selected.

Existing links are read and merged, never replaced - same rule as every other
write to an asset AgentLens does not own.
"""

import os
import sys

FILES = {}

# ===========================================================================
FILES["agentlens/server.py"] = '''"""Serve the explorer, and put a link to it inside DataHub.

Two small pieces:

``serve``
    A standard-library HTTP server. It rebuilds the page from the manifest on
    every request rather than serving a snapshot, so a change to a SKILL.md
    shows up on refresh. No framework, no dependency, no build step.

``link_tables``
    Writes an ``institutionalMemory`` element onto each catalogued table. That
    aspect is what DataHub's UI renders as **Links** in an entity's sidebar, so
    the explorer becomes reachable from inside localhost:9002 without patching
    or forking the frontend.

    Existing elements are read first and merged. This is an asset AgentLens
    does not own, and clobbering someone else's runbook link to add our own
    would be exactly the kind of silent damage this project is about.
"""

from __future__ import annotations

import json
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import quote

import requests
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata import schema_classes as sc

from .explorer import build_payload, render_html
from .impact import GMS, TOKEN, _aspect
from .model import Manifest

LINK_DESCRIPTION = "Simulate a change to this table (AgentLens)"


def explorer_url(base_url: str, table_urn: str) -> str:
    return f"{base_url.rstrip('/')}/?table={quote(table_urn, safe='')}"


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    manifest_path = "manifest.json"
    repo_root = "demo-repo"

    def do_GET(self) -> None:                       # noqa: N802 - stdlib name
        if self.path.split("?")[0] not in ("/", "/index.html"):
            self.send_error(404)
            return
        try:
            with open(self.manifest_path, encoding="utf-8") as fh:
                manifest = Manifest.from_dict(json.load(fh))
            body = render_html(build_payload(manifest, self.repo_root)).encode("utf-8")
        except (OSError, ValueError) as exc:
            self.send_error(500, f"could not build the page: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")   # rebuilt every request
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        pass                                        # the banner is enough


def serve(manifest_path: str, repo_root: str, port: int = 8000) -> None:
    handler = partial(_Handler)
    _Handler.manifest_path = manifest_path
    _Handler.repo_root = repo_root
    server = HTTPServer(("127.0.0.1", port), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


# ---------------------------------------------------------------------------
# link - make it reachable from the DataHub UI
# ---------------------------------------------------------------------------

def existing_links(urn: str) -> list[dict[str, Any]] | None:
    """Current sidebar links, or None if we could not find out.

    The distinction matters: ``institutionalMemory`` is a full-replace aspect,
    so writing it from a failed read would delete every link someone else put
    there. None means "do not write".
    """
    try:
        aspect = _aspect(urn, "institutionalMemory")
    except requests.RequestException:
        return None
    return list(aspect.get("elements", [])) if aspect else []


def merge_link(elements: list[dict[str, Any]], url: str,
               description: str = LINK_DESCRIPTION) -> tuple[list[dict[str, Any]], bool]:
    """Add our link if it isn't already there. Never drop anyone else's.

    Returns (elements, changed). Matching is on the description rather than the
    URL, so re-running with a different port updates in place instead of
    accumulating a link per port.
    """
    out = []
    changed = True
    for element in elements:
        if element.get("description") == description:
            if element.get("url") == url:
                changed = False
            continue                                # replaced below
        out.append(element)
    out.append({"url": url, "description": description})
    if not changed and len(out) == len(elements):
        return elements, False
    return out, True


def link_tables(manifest: Manifest, base_url: str, dry_run: bool = False) -> list[dict[str, Any]]:
    """Put the explorer in the sidebar of every table the fleet reads."""
    tables = sorted({
        r.resolved_urn for s in manifest.skills for r in s.data_refs if r.resolved_urn
    })
    emitter = None if dry_run else DatahubRestEmitter(gms_server=GMS, token=TOKEN or None)
    written = []

    for urn in tables:
        url = explorer_url(base_url, urn)
        current = existing_links(urn)
        if current is None:
            written.append({
                "urn": urn, "url": url,
                "status": "skipped" if not dry_run else "unknown",
                "note": "could not read the existing links; refusing to overwrite them",
            })
            continue
        elements, changed = merge_link(current, url)
        if not changed:
            written.append({"urn": urn, "url": url, "status": "already linked"})
            continue
        if dry_run:
            written.append({"urn": urn, "url": url, "status": "would link"})
            continue
        stamp = sc.AuditStampClass(time=int(time.time() * 1000), actor="urn:li:corpuser:agentlens")
        assert emitter is not None
        emitter.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=urn,
                aspect=sc.InstitutionalMemoryClass(elements=[
                    sc.InstitutionalMemoryMetadataClass(
                        url=e["url"],
                        description=e.get("description", ""),
                        createStamp=stamp,
                    )
                    for e in elements
                ]),
            )
        )
        written.append({"urn": urn, "url": url, "status": "linked"})
    return written
'''

# ===========================================================================
FILES["tests/test_server.py"] = '''"""Link merging and URL building. No server, no DataHub."""

from agentlens.model import DataRef, Manifest, Skill
from agentlens.server import LINK_DESCRIPTION, explorer_url, merge_link

URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.order_details,PROD)"


def _one_table_manifest():
    return Manifest(skills=[Skill(id="s", name="s", data_refs=[
        DataRef(raw="analytics.order_details", source_file="SKILL.md", resolved_urn=URN)])])
URL = "http://localhost:8000/?table=" + URN.replace(":", "%3A").replace(
    "(", "%28").replace(")", "%29").replace(",", "%2C")


def test_url_is_deep_linked_and_fully_escaped():
    url = explorer_url("http://localhost:8000", URN)
    assert url.startswith("http://localhost:8000/?table=")
    assert "(" not in url and "," not in url      # the urn must survive the query string


def test_trailing_slash_on_the_base_url_does_not_double_up():
    assert "//?table" not in explorer_url("http://localhost:8000/", URN)


def test_adds_our_link_to_an_empty_sidebar():
    elements, changed = merge_link([], "http://x/")
    assert changed
    assert elements == [{"url": "http://x/", "description": LINK_DESCRIPTION}]


def test_never_drops_someone_elses_link():
    theirs = {"url": "http://runbook/", "description": "On-call runbook"}
    elements, changed = merge_link([theirs], "http://x/")
    assert changed
    assert theirs in elements
    assert len(elements) == 2


def test_re_running_unchanged_is_a_no_op():
    first, _ = merge_link([], "http://x/")
    second, changed = merge_link(first, "http://x/")
    assert not changed
    assert second == first


def test_an_unreadable_sidebar_is_never_merged_into():
    """institutionalMemory is full-replace: writing from a failed read deletes links."""
    from agentlens import server

    def boom(_urn):
        return None

    original, server.existing_links = server.existing_links, boom
    try:
        rows = server.link_tables(_one_table_manifest(), "http://localhost:8000", dry_run=True)
    finally:
        server.existing_links = original
    assert [r["status"] for r in rows] == ["unknown"]
    assert "refusing to overwrite" in rows[0]["note"]


def test_a_new_port_updates_in_place_rather_than_accumulating():
    first, _ = merge_link([], "http://localhost:8000/")
    second, changed = merge_link(first, "http://localhost:9999/")
    assert changed
    assert len(second) == 1
    assert second[0]["url"] == "http://localhost:9999/"
'''


# ===========================================================================
CMD = '''

def cmd_serve(args) -> int:
    """Serve the explorer on localhost, rebuilt on every request."""
    url = f"http://localhost:{args.port}"
    print(f"  AgentLens explorer on {url}")
    print(f"  rebuilding from {args.manifest} on every request - edit a SKILL.md and refresh")
    print("  ctrl-c to stop\\n")
    if args.link:
        with open(args.manifest) as fh:
            manifest = Manifest.from_dict(json.load(fh))
        for row in link_tables(manifest, url):
            print(f"  {row['status']:14s} {row['urn']}")
            if row.get("note"):
                print(f"                 ! {row['note']}")
        print()
    serve(args.manifest, args.repo, args.port)
    return 0


def cmd_link(args) -> int:
    """Put the explorer in the sidebar of every table the fleet reads."""
    with open(args.manifest) as fh:
        manifest = Manifest.from_dict(json.load(fh))

    rows = link_tables(manifest, args.base_url, dry_run=args.dry_run)
    for row in rows:
        print(f"  {row['status']:14s} {row['urn']}")
        if row.get("note"):
            print(f"                 ! {row['note']}")
    if not rows:
        print("  no resolved tables in the manifest - run scan and emit first")
        return 1

    print("\\n  Open any of those in DataHub and look for \\"Links\\" in the sidebar.")
    print("  The explorer must be running: python -m agentlens.cli serve")
    return 0
'''

PARSER = '''    p = sub.add_parser("serve", help="serve the explorer on localhost")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--repo", default="demo-repo")
    p.add_argument("--manifest", default="manifest.json")
    p.add_argument("--link", action="store_true",
                   help="also write the DataHub sidebar links before serving")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("link", help="link the explorer from every table in DataHub's UI")
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--manifest", default="manifest.json")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_link)

'''


def patch_cli() -> bool:
    path = "agentlens/cli.py"
    if not os.path.exists(path):
        print("  MISS   cli.py not found")
        return False
    with open(path, encoding="utf-8") as fh:
        cli = fh.read()

    if "cmd_serve" in cli:
        print("  ok     cli: serve/link (already applied)")
        return True

    imp = "from .scanner import scan"
    if imp not in cli:
        print("  MISS   cli: scanner import line")
        return False
    cli = cli.replace(imp, imp + "\nfrom .server import link_tables, serve", 1)

    anchor = "\n\ndef main(argv=None) -> int:"
    if anchor not in cli:
        print("  MISS   cli: main()")
        return False
    cli = cli.replace(anchor, "\n" + CMD + anchor, 1)

    p_anchor = '    p = sub.add_parser("guard"'
    if p_anchor not in cli:
        print("  MISS   cli: guard subparser")
        return False
    cli = cli.replace(p_anchor, PARSER + p_anchor, 1)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(cli)
    print("  patch  cli: serve and link commands")
    return True


def main() -> int:
    if not os.path.isdir("agentlens"):
        print("Run this from inside agentlens-project/.")
        return 1
    if not os.path.exists("agentlens/explorer.py"):
        print("Run add_explorer.py first.")
        return 1
    with open("agentlens/explorer.py", encoding="utf-8") as fh:
        if "URLSearchParams" not in fh.read():
            print("Re-run add_explorer.py first - the page needs deep-link support.")
            return 1

    for rel, content in FILES.items():
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        with open(rel, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"  write  {rel}")

    patch_cli()

    print("""
  Done. Two terminals:

      python -m agentlens.cli serve --link      # serves, and writes the links
      # then open http://localhost:9002 and go to any table your agents read

  In DataHub, the table's sidebar now has a Links entry:

      Simulate a change to this table (AgentLens)

  Clicking it opens the explorer with that table already selected. Existing
  links on those tables are read and merged, never replaced.

  To see what it would write without writing:

      python -m agentlens.cli link --dry-run
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
