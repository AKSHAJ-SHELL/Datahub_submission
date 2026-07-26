"""Write the agent fleet into DataHub as a real, traversable subgraph.

Modelling note (this belongs in the README too):

DataHub defines native `aiAgent`, `agentSkill`, and `api` entity types in
entity-registry.yml on master, but they are absent from the v1.6.0 entity
registry and have no Python SDK classes - emitting one returns
`400 Failed to find entity with name aiAgent in EntityRegistry`.

So we model agents and skills as datasets in a dedicated `agentlens` platform,
distinguished by subtype, with agent metadata in custom properties. This is a
standard DataHub pattern for non-table assets. Crucially, `upstreamLineage` is
dataset-to-dataset, so every traversal in impact.py is identical to what it
would be against native entities. When those ship, only this file changes.
"""

from __future__ import annotations

import os

import requests
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata import schema_classes as sc

PLATFORM = "agentlens"
ENV = "PROD"

GMS = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080").rstrip("/")
TOKEN = os.environ.get("DATAHUB_GMS_TOKEN", "")

HEADERS = {"Content-Type": "application/json"}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"

SUBTYPE_AGENT = "AI Agent"
SUBTYPE_SKILL = "Agent Skill"
SUBTYPE_TOOL = "MCP Tool"


def urn_for(kind: str, ident: str) -> str:
    safe = ident.replace(" ", "-").replace("/", "_")
    return f"urn:li:dataset:(urn:li:dataPlatform:{PLATFORM},{kind}.{safe},{ENV})"


class Emitter:
    def __init__(self) -> None:
        self.rest = DatahubRestEmitter(gms_server=GMS, token=TOKEN or None)
        self.emitted: list[str] = []

    # -- platform ---------------------------------------------------------
    def register_platform(self) -> None:
        self.rest.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=f"urn:li:dataPlatform:{PLATFORM}",
                aspect=sc.DataPlatformInfoClass(
                    name=PLATFORM,
                    displayName="AgentLens",
                    type=sc.PlatformTypeClass.OTHERS,
                    datasetNameDelimiter=".",
                ),
            )
        )

    # -- generic node -----------------------------------------------------
    def _node(self, urn: str, display: str, description: str,
              subtype: str, props: dict[str, str]) -> None:
        clean = {k: str(v) for k, v in props.items() if v not in (None, "", [])}
        self.rest.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=urn,
                aspect=sc.DatasetPropertiesClass(
                    name=display, description=description, customProperties=clean
                ),
            )
        )
        self.rest.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=urn, aspect=sc.SubTypesClass(typeNames=[subtype])
            )
        )
        self.emitted.append(urn)

    # -- lineage ----------------------------------------------------------
    def lineage(self, downstream: str, upstreams: list[str]) -> None:
        if not upstreams:
            return
        self.rest.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=downstream,
                aspect=sc.UpstreamLineageClass(
                    upstreams=[
                        sc.UpstreamClass(
                            dataset=u, type=sc.DatasetLineageTypeClass.TRANSFORMED
                        )
                        for u in dict.fromkeys(upstreams)
                    ]
                ),
            )
        )

    # -- tags / deprecation (the write-back half) -------------------------
    def tag(self, urn: str, tag: str) -> None:
        tag_urn = f"urn:li:tag:{tag}"
        self.rest.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=tag_urn, aspect=sc.TagPropertiesClass(name=tag)
            )
        )
        self.rest.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=urn,
                aspect=sc.GlobalTagsClass(
                    tags=[sc.TagAssociationClass(tag=tag_urn)]
                ),
            )
        )

    def deprecate(self, urn: str, note: str) -> None:
        self.rest.emit_mcp(
            MetadataChangeProposalWrapper(
                entityUrn=urn,
                aspect=sc.DeprecationClass(
                    deprecated=True,
                    note=note,
                    actor="urn:li:corpuser:datahub",
                ),
            )
        )

    # -- manifest ---------------------------------------------------------
    def emit_manifest(self, manifest) -> dict[str, str]:
        self.register_platform()
        index: dict[str, str] = {}

        for tool in manifest.tools:
            urn = urn_for("tool", f"{tool.server}.{tool.name}")
            index[f"tool:{tool.name}"] = urn
            self._node(
                urn, tool.name,
                f"MCP tool `{tool.name}` from server `{tool.server}`.\n\n_Catalogued by AgentLens._",
                SUBTYPE_TOOL,
                {"agentlens.kind": "tool", "agentlens.server": tool.server,
                 "agentlens.source_file": tool.source_file},
            )

        for skill in manifest.skills:
            urn = urn_for("skill", skill.id)
            index[f"skill:{skill.id}"] = urn
            self._node(
                urn, skill.name,
                (skill.description or "No description.") + "\n\n_Catalogued by AgentLens._",
                SUBTYPE_SKILL,
                {
                    "agentlens.kind": "skill",
                    "agentlens.source_repository": skill.source_repository,
                    "agentlens.source_path": skill.source_path,
                    "agentlens.instructions_sha": skill.instructions_sha,
                    "agentlens.data_refs": ", ".join(
                        r.raw for r in skill.data_refs if r.resolved_urn
                    ),
                },
            )
            upstreams = [r.resolved_urn for r in skill.data_refs if r.resolved_urn]
            upstreams += [index[f"tool:{t}"] for t in skill.tools if f"tool:{t}" in index]
            self.lineage(urn, upstreams)

        for agent in manifest.agents:
            urn = urn_for("agent", agent.id)
            index[f"agent:{agent.id}"] = urn
            self._node(
                urn, agent.name,
                (agent.description or "No description.") + "\n\n_Catalogued by AgentLens._",
                SUBTYPE_AGENT,
                {
                    "agentlens.kind": "agent",
                    "agentlens.source_repository": agent.source_repository,
                    "agentlens.source_path": agent.source_path,
                    "agentlens.model": agent.model,
                    "agentlens.owner_team": agent.owner_team,
                    "agentlens.skills": ", ".join(agent.skills),
                },
            )
            upstreams = [index[f"skill:{s}"] for s in agent.skills if f"skill:{s}" in index]
            upstreams += [index[f"tool:{t}"] for t in agent.tools if f"tool:{t}" in index]
            self.lineage(urn, upstreams)

            # Companion `application` entity - a supported native type.
            try:
                requests.post(
                    f"{GMS}/openapi/v3/entity/application",
                    headers=HEADERS,
                    json=[{
                        "urn": f"urn:li:application:agentlens-{agent.id}",
                        "applicationProperties": {
                            "value": {"name": agent.name,
                                      "description": agent.description or "AI agent."}
                        },
                    }],
                    timeout=20,
                )
            except Exception:
                pass

        return index
