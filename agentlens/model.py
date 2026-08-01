"""Data model for the agent fleet.

Deliberately small. The scanner produces a Manifest, the emitter consumes it.
Anything that is not needed to compute blast radius does not belong here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DataRef:
    """A reference to a data asset found in a skill or prompt."""

    raw: str  # the token as it appeared, e.g. "analytics.order_details"
    source_file: str  # where we found it
    confidence: float = 0.5  # how sure we are this is a real table
    resolved_urn: str | None = None


@dataclass
class Tool:
    name: str
    server: str  # MCP server name
    source_file: str


@dataclass
class Skill:
    id: str
    name: str
    description: str = ""
    source_repository: str = ""
    source_path: str = ""
    instructions_sha: str = ""
    data_refs: list[DataRef] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


@dataclass
class Agent:
    id: str
    name: str
    description: str = ""
    source_repository: str = ""
    source_path: str = ""
    model: str = ""
    owner_team: str = ""
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    agents: list[Agent] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)
    repository: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Manifest:
        return Manifest(
            repository=data.get("repository", ""),
            agents=[Agent(**a) for a in data.get("agents", [])],
            skills=[
                Skill(
                    **{
                        **s,
                        "data_refs": [DataRef(**d) for d in s.get("data_refs", [])],
                    }
                )
                for s in data.get("skills", [])
            ],
            tools=[Tool(**t) for t in data.get("tools", [])],
        )

    def summary(self) -> str:
        refs = sum(len(s.data_refs) for s in self.skills)
        return (
            f"{len(self.agents)} agents, {len(self.skills)} skills, "
            f"{len(self.tools)} tools, {refs} data references"
        )
