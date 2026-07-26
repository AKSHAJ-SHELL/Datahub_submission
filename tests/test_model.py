"""Manifest serialisation."""

from agentlens.model import Agent, DataRef, Manifest, Skill, Tool


def test_round_trip_preserves_everything():
    original = Manifest(
        repository="acme/agents",
        agents=[Agent(id="a", name="a", skills=["s"], owner_team="team")],
        skills=[
            Skill(
                id="s", name="s",
                data_refs=[DataRef(raw="analytics.orders", source_file="x.md",
                                   confidence=0.9, resolved_urn="urn:li:dataset:(x)")],
            )
        ],
        tools=[Tool(name="t", server="srv", source_file=".mcp.json")],
    )

    restored = Manifest.from_dict(original.to_dict())

    assert restored.repository == "acme/agents"
    assert restored.agents[0].owner_team == "team"
    assert restored.skills[0].data_refs[0].resolved_urn == "urn:li:dataset:(x)"
    assert restored.skills[0].data_refs[0].confidence == 0.9
    assert restored.tools[0].server == "srv"


def test_empty_manifest_round_trips():
    assert Manifest.from_dict(Manifest().to_dict()).agents == []


def test_summary_counts_data_refs():
    m = Manifest(skills=[Skill(id="s", name="s",
                              data_refs=[DataRef(raw="a.b", source_file="x")])])
    assert "1 data reference" in m.summary()
