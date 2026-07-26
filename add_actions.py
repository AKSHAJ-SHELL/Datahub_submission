#!/usr/bin/env python3
"""
Adds the write-back half of AgentLens.

Run from inside agentlens-project/:

    python add_actions.py

Writes agentlens/actions.py and replaces agentlens/cli.py to add `guard`.
"""

import os
import sys

FILES = {}

# ===========================================================================
FILES["agentlens/actions.py"] = '''"""The act-and-write-back half.

DataHub's own framing of what a good agent does:

    "read DataHub to understand what's connected to what, take action, and
     write results back so the next person or agent inherits the context."

Reading is impact.py. This is the other two thirds.

The important write is on the *upstream warehouse table*, not on our own
entities. After a guard run, a real Snowflake table in the catalog carries a
tag saying three AI agents depend on it - information that did not exist in
the graph before and that anyone browsing DataHub now inherits.

All writes to assets we do not own go through DatasetPatchBuilder, which is
additive. A plain GlobalTagsClass emit would silently wipe whatever tags the
table already had.
"""

from __future__ import annotations

import json
import os
import time

import requests
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata import schema_classes as sc

GMS = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080").rstrip("/")
TOKEN = os.environ.get("DATAHUB_GMS_TOKEN", "")

TAG_CONSUMERS = "has-agent-consumers"
TAG_REVIEW = "agent-context-review"


class Actions:
    def __init__(self) -> None:
        self.rest = DatahubRestEmitter(gms_server=GMS, token=TOKEN or None)
        self.log: list[str] = []

    # -- helpers ----------------------------------------------------------
    def _ensure_tag(self, tag: str, description: str) -> str:
        urn = f"urn:li:tag:{tag}"
        self.rest.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=urn,
                aspect=sc.TagPropertiesClass(name=tag, description=description),
            )
        )
        return urn

    def _add_tag_safely(self, dataset_urn: str, tag_urn: str) -> bool:
        """Additive tag write. Never clobbers existing tags."""
        try:
            from datahub.specific.dataset import DatasetPatchBuilder

            builder = DatasetPatchBuilder(dataset_urn)
            builder.add_tag(sc.TagAssociationClass(tag=tag_urn))
            for mcp in builder.build():
                self.rest.emit_mcp(mcp)
            return True
        except Exception as exc:
            self.log.append(f"    patch failed for {dataset_urn}: {exc}")
            return False

    # -- the writes -------------------------------------------------------
    def flag_upstream(self, report: dict, reason: str) -> None:
        """Tag the changed warehouse table with its agent consumers.

        This is the contribution back to the graph. The table is not ours; the
        knowledge that agents depend on it is new.
        """
        agents = report["agents"]
        if not agents:
            self.log.append("  no agents downstream - nothing to record upstream")
            return

        tag_urn = self._ensure_tag(
            TAG_CONSUMERS,
            "One or more AI agents read this asset. Discovered by AgentLens.",
        )
        if self._add_tag_safely(report["root"], tag_urn):
            names = ", ".join(a["name"] for a in agents)
            self.log.append(f"  tagged upstream `{TAG_CONSUMERS}`  ({names})")

    def flag_affected(self, report: dict, reason: str, deprecate: bool = False) -> None:
        """Mark every downstream agent and skill for review."""
        affected = report["agents"] + report["skills"] + report["tools"]
        if not affected:
            return

        tag_urn = self._ensure_tag(
            TAG_REVIEW,
            "Upstream data changed. This agent's context may be stale.",
        )

        for item in affected:
            self._add_tag_safely(item["urn"], tag_urn)

            note = (
                f"Upstream change: {reason}\\n"
                f"Source: {report['root']}\\n"
                f"Distance: {item['hops']} hop(s)"
            )
            try:
                self.rest.emit_mcp(
                    MetadataChangeProposalWrapper(
                        entityUrn=item["urn"],
                        aspect=sc.InstitutionalMemoryClass(
                            elements=[
                                sc.InstitutionalMemoryMetadataClass(
                                    url=f"{GMS}/entity/{report['root']}",
                                    description=note[:200],
                                    createStamp=sc.AuditStampClass(
                                        time=int(time.time() * 1000),
                                        actor="urn:li:corpuser:datahub",
                                    ),
                                )
                            ]
                        ),
                    )
                )
            except Exception as exc:
                self.log.append(f"    memory failed for {item['name']}: {exc}")

            if deprecate:
                try:
                    self.rest.emit_mcp(
                        MetadataChangeProposalWrapper(
                            entityUrn=item["urn"],
                            aspect=sc.DeprecationClass(
                                deprecated=True,
                                note=f"Context stale: {reason}",
                                actor="urn:li:corpuser:datahub",
                            ),
                        )
                    )
                except Exception as exc:
                    self.log.append(f"    deprecate failed for {item['name']}: {exc}")

        verb = "deprecated" if deprecate else "tagged"
        self.log.append(f"  {verb} {len(affected)} downstream asset(s) `{TAG_REVIEW}`")

    # -- act outside DataHub ---------------------------------------------
    def file_github_issue(self, report: dict, reason: str, repo: str) -> str | None:
        """Open an issue on the repo that owns the affected agents."""
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            self.log.append("  GITHUB_TOKEN not set - skipping issue")
            return None

        agents = report["agents"]
        body = [
            f"An upstream data change affects **{len(agents)} agent(s)** in this repository.",
            "",
            f"**Change:** {reason}",
            f"**Source asset:** `{report['root']}`",
            "",
            "### Affected",
            "",
            "| Asset | Kind | Hops | Team | Path |",
            "|---|---|---|---|---|",
        ]
        for item in agents + report["skills"]:
            body.append(
                f"| {item['name']} | {item['kind']} | {item['hops']} | "
                f"{item.get('owner_team') or '-'} | `{item.get('source_path') or '-'}` |"
            )
        body += ["", "---", "", "_Filed automatically by AgentLens._"]

        try:
            resp = requests.post(
                f"https://api.github.com/repos/{repo}/issues",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                json={
                    "title": f"[AgentLens] {len(agents)} agent(s) affected: {reason[:60]}",
                    "body": "\\n".join(body),
                },
                timeout=30,
            )
            if resp.status_code < 300:
                url = resp.json().get("html_url")
                self.log.append(f"  filed GitHub issue: {url}")
                return url
            self.log.append(f"  GitHub issue failed: {resp.status_code} {resp.text[:160]}")
        except Exception as exc:
            self.log.append(f"  GitHub issue failed: {exc}")
        return None

    def render_log(self) -> str:
        if not self.log:
            return "  (nothing written)"
        return "\\n".join(self.log)
'''

# ===========================================================================
FILES["agentlens/cli.py"] = '''"""AgentLens CLI."""

from __future__ import annotations

import argparse
import json
import sys

from .actions import Actions
from .emitter import Emitter
from .impact import blast_radius, render
from .model import Manifest
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
    return 0


def cmd_guard(args) -> int:
    """The full loop: read the graph, decide, act, write back."""
    print("\\n[1/3] READ - walking downstream lineage")
    report = blast_radius(args.urn, max_hops=args.hops)
    print(render(report))

    n_agents = len(report["agents"])
    if n_agents == 0 and not args.force:
        print("[2/3] DECIDE - no agents affected, no action taken\\n")
        return 0

    print(f"[2/3] DECIDE - {n_agents} agent(s) affected, acting")
    if args.dry_run:
        print("        --dry-run set, no writes performed\\n")
        return 0

    print("\\n[3/3] WRITE BACK")
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
        print(f"\\n  wrote {args.json}")

    print("\\n  The next person or agent to open these assets in DataHub")
    print("  inherits this context.\\n")
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
    p.set_defaults(func=cmd_guard)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
'''


def main() -> int:
    if not os.path.isdir("agentlens"):
        print("Run this from inside agentlens-project/ (no agentlens/ dir here).")
        return 1

    for rel, content in FILES.items():
        with open(rel, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"  write  {rel}")

    print("""
Now run the full loop:

    python -m agentlens.cli guard \\
      "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.analytics.order_details,PROD)" \\
      --reason "dropping column discount_pct" \\
      --json examples/guard-run.json
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
