"""AgentLens CLI."""

from __future__ import annotations

import argparse
import json
import sys

from .actions import Actions
from .emitter import Emitter
from .impact import blast_radius, render
from .model import Manifest
from .report import write_html
from .resolver import Resolver
from .scanner import scan


def cmd_scan(args) -> int:
    manifest = scan(args.repo, args.repository or "")
    print(f"scanned {args.repo}: {manifest.summary()}")

    if not args.no_resolve:
        resolver = Resolver()
        resolved, total = resolver.resolve_manifest(manifest)
        print(f"resolved {resolved}/{total} data references against DataHub")

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
    report = blast_radius(args.urn, max_hops=args.hops)
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
    report = blast_radius(args.urn, max_hops=args.hops)
    print(render(report))

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
            json.dump({"report": report, "reason": args.reason,
                       "actions": actions.log}, fh, indent=2)
        print(f"\n  wrote {args.json}")
    if args.html:
        write_html(report, args.html, reason=args.reason, actions=actions.log)
        print(f"  wrote {args.html}")

    print("\n  The next person or agent to open these assets in DataHub")
    print("  inherits this context.\n")
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

    p = sub.add_parser("emit", help="write a manifest into DataHub")
    p.add_argument("manifest", nargs="?", default="manifest.json")
    p.set_defaults(func=cmd_emit)

    p = sub.add_parser("impact", help="blast radius for a changed asset (read only)")
    p.add_argument("urn")
    p.add_argument("--hops", type=int, default=6)
    p.add_argument("--json", help="also write the report as JSON")
    p.add_argument("--html", help="also write a self-contained HTML report")
    p.set_defaults(func=cmd_impact)

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
    p.set_defaults(func=cmd_guard)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
