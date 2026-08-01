"""The one rule: never report safety from a traversal we could not finish.

No DataHub required - these drive render() with hand-built reports, which is
the whole point. The failure being guarded against is a *silent* one, so it
needs a test that fails loudly.
"""

from agentlens.impact import render

BASE = {
    "root": "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.orders,PROD)",
    "agents": [], "skills": [], "tools": [],
    "total_downstream": 0, "total_downstream_scope": "agentlens-subgraph",
    "source": "aspects",
}


def test_failed_traversal_never_says_safe():
    out = render({**BASE, "ok": False, "nodes_examined": 0,
                  "warnings": ["could not reach GMS"]})
    assert "Safe to change" not in out
    assert "TRAVERSAL INCOMPLETE" in out
    assert "could not reach GMS" in out


def test_zero_nodes_examined_never_says_safe():
    """The stale-cache case: the walk completed, but over an empty graph."""
    out = render({**BASE, "ok": True, "nodes_examined": 0, "warnings": []})
    assert "Safe to change" not in out
    assert "not a clean bill of health" in out


def test_genuinely_clear_asset_does_say_safe():
    out = render({**BASE, "ok": True, "nodes_examined": 12, "warnings": []})
    assert "Safe to change" in out
    assert "12 catalogued AgentLens node(s) were checked" in out


def test_affected_agents_are_named_with_their_team():
    out = render({
        **BASE, "ok": True, "nodes_examined": 12, "warnings": [],
        "agents": [{"urn": "u", "name": "finance-copilot", "kind": "agent",
                    "subtype": "AI Agent", "repository": "github.com/acme/data-agents",
                    "source_path": "agentlens.yaml", "owner_team": "fpa-platform",
                    "hops": 2}],
    })
    assert "finance-copilot" in out
    assert "fpa-platform" in out
    assert "Safe to change" not in out


def test_warnings_are_always_surfaced():
    out = render({**BASE, "ok": True, "nodes_examined": 3,
                  "warnings": ["using Dataset.lineage, which is cached"],
                  "source": "graphql"})
    assert "cached" in out
    assert "read via: graphql" in out
