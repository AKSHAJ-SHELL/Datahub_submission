"""AgentLens CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .actions import Actions
from .drift import (
    catalogued_urns,
    compare,
    expected_state,
    read_catalog,
)
from .drift import render as drift_render
from .emitter import Emitter
from .explorer import write as explorer_write
from .impact import blast_radius, render
from .model import Manifest
from .report import write_html
from .resolver import Resolver
from .sandbox import (
    Change,
    fork,
    resolve_table,
    simulate,
    table_candidates,
)
from .sandbox import render as sandbox_render
from .scanner import scan
from .server import link_tables, serve


def cmd_scan(args) -> int:
    manifest = scan(args.repo, args.repository or "")
    print(f"scanned {args.repo}: {manifest.summary()}")

    if not args.no_resolve:
        resolver = Resolver()
        resolved, total = resolver.resolve_manifest(manifest)
        strong = sum(1 for s in manifest.skills for r in s.data_refs if r.confidence >= 0.9)
        print(f"resolved {resolved}/{total} data references against DataHub")
        if total:
            print(f"  ({strong} came from SQL FROM/JOIN clauses)")
        unresolved = [r.raw for s in manifest.skills for r in s.data_refs if not r.resolved_urn]
        if unresolved:
            preview = ", ".join(sorted(unresolved)[:6])
            more = "" if len(unresolved) <= 6 else f" (+{len(unresolved) - 6} more)"
            print(f"  unresolved: {preview}{more}")

    with open(args.out, "w") as fh:
        json.dump(manifest.to_dict(), fh, indent=2)
    print(f"wrote {args.out}")
    return 0


def cmd_emit(args) -> int:
    with open(args.manifest) as fh:
        manifest = Manifest.from_dict(json.load(fh))

    emitter = Emitter()
    index = emitter.emit_manifest(manifest)
    print(f"emitted {len(emitter.emitted)} entities to DataHub")
    for key, urn in sorted(index.items()):
        print(f"  {key:32s} {urn}")
    return 0


def cmd_impact(args) -> int:
    report = blast_radius(args.urn, max_hops=args.hops,
                           source=getattr(args, "lineage_source", "aspects"))
    print(render(report))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"wrote {args.json}")
    if args.html:
        write_html(report, args.html, reason="(read-only impact check)")
        print(f"wrote {args.html}")
    return 0


def cmd_guard(args) -> int:
    """The full loop: read the graph, decide, act, write back."""
    print("\n[1/3] READ - walking downstream lineage")
    report = blast_radius(args.urn, max_hops=args.hops,
                           source=getattr(args, "lineage_source", "aspects"))
    print(render(report))

    if not report.get("ok", True) and not args.force:
        print("[2/3] DECIDE - traversal incomplete, refusing to report a result\n")
        print("        re-run once the warnings above are resolved, or pass --force\n")
        return 1

    if not report.get("ok", True) and not args.force:
        print("[2/3] DECIDE - traversal incomplete, refusing to report a result\n")
        print("        re-run once the warnings above are resolved, or pass --force\n")
        return 1

    n_agents = len(report["agents"])
    if n_agents == 0 and not args.force:
        print("[2/3] DECIDE - no agents affected, no action taken\n")
        return 0

    print(f"[2/3] DECIDE - {n_agents} agent(s) affected, acting")
    if args.dry_run:
        print("        --dry-run set, no writes performed\n")
        return 0

    print("\n[3/3] WRITE BACK")
    actions = Actions()
    actions.flag_upstream(report, args.reason)
    actions.flag_affected(report, args.reason, deprecate=args.deprecate)
    if args.github_repo:
        actions.file_github_issue(report, args.reason, args.github_repo)
    print(actions.render_log())

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(
                {"report": report, "reason": args.reason, "actions": actions.log},
                fh,
                indent=2,
            )
        print(f"\n  wrote {args.json}")
    if args.html:
        write_html(report, args.html, reason=args.reason, actions=actions.log)
        print(f"  wrote {args.html}")

    print("\n  The next person or agent to open these assets in DataHub")
    print("  inherits this context.\n")
    return 0



def cmd_drift(args) -> int:
    """Re-scan, compare against the catalog, report the delta."""
    manifest = scan(args.repo, args.repository or "")
    if not args.no_resolve:
        Resolver().resolve_manifest(manifest)

    expected = expected_state(manifest)
    catalog = read_catalog(list(expected))
    known = catalogued_urns() if not args.no_orphans else None
    changes = compare(expected, catalog, known)

    if args.format == "json":
        print(json.dumps({"changes": [c.to_dict() for c in changes],
                          "scanned": len(expected)}, indent=2))
    else:
        print(drift_render(changes, len(expected)))

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"changes": [c.to_dict() for c in changes],
                       "scanned": len(expected)}, fh, indent=2)

    return 1 if changes and not args.exit_zero else 0



def cmd_sandbox(args) -> int:
    """Simulate a change that does not exist yet. Writes nothing by default."""
    with open(args.manifest) as fh:
        manifest = Manifest.from_dict(json.load(fh))

    table = resolve_table(manifest, args.table)
    if not table:
        print(f"Could not resolve {args.table!r} to a single table. Candidates:")
        for urn in table_candidates(manifest):
            print(f"  {urn}")
        return 1

    if args.drop_column:
        change = Change("drop-column", table, column=args.drop_column)
    elif args.rename_to:
        change = Change("rename-table", table, new_name=args.rename_to)
    else:
        change = Change("drop-table", table)

    report = simulate(fork(manifest, args.repo), change, max_hops=args.hops)
    print(sandbox_render(report))

    if args.promote:
        reason = args.reason or report["description"]
        print("  PROMOTE - writing the finding back")
        actions = Actions()
        actions.flag_upstream(report, reason)
        actions.flag_affected(report, reason, deprecate=False)
        print(actions.render_log())
        report["written"] = True

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"  wrote {args.json}")

    return 1 if report["counts"]["breaks"] and not args.exit_zero else 0



def cmd_explore(args) -> int:
    """Build a self-contained, clickable view of the sandbox."""
    with open(args.manifest) as fh:
        manifest = Manifest.from_dict(json.load(fh))

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    payload = explorer_write(manifest, args.repo, args.out)

    print(f"wrote {args.out}")
    print(f"  {len(payload['tables'])} table(s), {len(payload['columns'])} column(s) discovered")
    if payload["unresolved"]:
        print(f"  unresolved, shown on the page: {', '.join(payload['unresolved'])}")
    print(f"\n  open {args.out}")
    return 0



def cmd_serve(args) -> int:
    """Serve the explorer on localhost, rebuilt on every request."""
    url = f"http://localhost:{args.port}"
    print(f"  AgentLens explorer on {url}")
    print(f"  fleet payload  on {url}/payload.json  (what the DataHub Agents tab reads)")
    print(f"  rebuilding from {args.manifest} on every request - edit a SKILL.md and refresh")
    print("  ctrl-c to stop\n")
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

    print("\n  Open any of those in DataHub and look for \"Links\" in the sidebar.")
    print("  The explorer must be running: python -m agentlens.cli serve")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="agentlens", description="Lineage for AI agents.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="scan a repo into a manifest")
    p.add_argument("repo")
    p.add_argument("-o", "--out", default="manifest.json")
    p.add_argument("--repository", help="repo name to record, e.g. github.com/acme/agents")
    p.add_argument("--no-resolve", action="store_true", help="skip DataHub URN resolution")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("drift", help="has the catalog gone stale since the last emit?")
    p.add_argument("repo")
    p.add_argument("--repository", help="repo name to record, e.g. github.com/acme/agents")
    p.add_argument("--no-resolve", action="store_true", help="skip DataHub URN resolution")
    p.add_argument("--no-orphans", action="store_true",
                   help="skip the search for catalogued nodes whose source is gone")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--json", help="also write the changes as JSON")
    p.add_argument("--exit-zero", action="store_true",
                   help="always exit 0; by default drift exits 1 so CI can gate on it")
    p.set_defaults(func=cmd_drift)

    p = sub.add_parser("emit", help="write a manifest into DataHub")
    p.add_argument("manifest", nargs="?", default="manifest.json")
    p.set_defaults(func=cmd_emit)

    p = sub.add_parser("impact", help="blast radius for a changed asset (read only)")
    p.add_argument("urn")
    p.add_argument("--hops", type=int, default=6)
    p.add_argument("--json", help="also write the report as JSON")
    p.add_argument("--html", help="also write a self-contained HTML report")
    p.add_argument(
        "--lineage-source",
        choices=["aspects", "graphql"],
        default="aspects",
        help="aspects (default) reads stored upstreamLineage and is cache-free; "
             "graphql uses Dataset.lineage, which is cached with no opt-out",
    )
    p.set_defaults(func=cmd_impact)

    p = sub.add_parser("sandbox", help="simulate a change before making it (writes nothing)")
    p.add_argument("table", help="table urn, or a fragment like order_details")
    p.add_argument("--repo", default="demo-repo", help="repo root the manifest was scanned from")
    p.add_argument("--manifest", default="manifest.json")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--drop-column", help="simulate dropping this column")
    g.add_argument("--rename-to", help="simulate renaming the table to this")
    g.add_argument("--drop-table", action="store_true", help="simulate dropping the table")
    p.add_argument("--hops", type=int, default=6)
    p.add_argument("--promote", action="store_true", help="write the finding back to DataHub")
    p.add_argument("--reason", help="recorded verbatim on the write-back")
    p.add_argument("--json", help="write the simulation as JSON")
    p.add_argument("--exit-zero", action="store_true",
                   help="always exit 0; by default a break exits 1 so CI can gate on it")
    p.set_defaults(func=cmd_sandbox)

    p = sub.add_parser("explore", help="build a clickable HTML view of the sandbox")
    p.add_argument("--repo", default="demo-repo", help="repo root the manifest was scanned from")
    p.add_argument("--manifest", default="manifest.json")
    p.add_argument("-o", "--out", default="examples/explorer.html")
    p.set_defaults(func=cmd_explore)

    p = sub.add_parser("serve", help="serve the explorer on localhost")
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

    p = sub.add_parser("guard", help="read, act, and write results back")
    p.add_argument("urn")
    p.add_argument("--reason", required=True, help='e.g. "dropping column discount_pct"')
    p.add_argument("--hops", type=int, default=6)
    p.add_argument("--deprecate", action="store_true", help="also mark affected assets deprecated")
    p.add_argument("--github-repo", help="owner/name - files an issue (needs GITHUB_TOKEN)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="act even if no agents affected")
    p.add_argument("--json", help="write the full run as JSON")
    p.add_argument("--html", help="also write a self-contained HTML report")
    p.add_argument(
        "--lineage-source",
        choices=["aspects", "graphql"],
        default="aspects",
        help="aspects (default) reads stored upstreamLineage and is cache-free; "
             "graphql uses Dataset.lineage, which is cached with no opt-out",
    )
    p.set_defaults(func=cmd_guard)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
