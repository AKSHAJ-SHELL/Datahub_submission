"""The act-and-write-back half.

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
                f"Upstream change: {reason}\n"
                f"Source: {report['root']}\n"
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
                    "body": "\n".join(body),
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
        return "\n".join(self.log)
