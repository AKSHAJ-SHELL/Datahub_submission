"""Serve the explorer, and put a link to it inside DataHub.

Two small pieces:

``serve``
    A standard-library HTTP server. It rebuilds the page from the manifest on
    every request rather than serving a snapshot, so a change to a SKILL.md
    shows up on refresh. No framework, no dependency, no build step.

    Two routes. ``/`` is the standalone page. ``/payload.json`` is the same data
    without the page around it, which is what the DataHub UI tab fetches — the
    tab runs the simulation itself, so it wants the graph, not our HTML.

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
        route = self.path.split("?")[0]
        if route in ("/", "/index.html"):
            kind = "text/html; charset=utf-8"
        elif route == "/payload.json":
            kind = "application/json"
        else:
            self.send_error(404)
            return

        try:
            payload = build_payload(self._manifest(), self.repo_root)
        except (OSError, ValueError) as exc:
            self.send_error(500, f"could not build the payload: {exc}")
            return

        body = (
            json.dumps(payload).encode("utf-8") if route == "/payload.json"
            else render_html(payload).encode("utf-8")
        )
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")   # rebuilt every request
        # The UI tab is served same-origin through vite's proxy in dev and
        # through datahub-frontend in a real deployment, so it never needs this.
        # A judge opening the page straight off :8000 does.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _manifest(self) -> Manifest:
        with open(self.manifest_path, encoding="utf-8") as fh:
            return Manifest.from_dict(json.load(fh))

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
