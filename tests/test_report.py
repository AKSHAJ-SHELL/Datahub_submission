"""HTML report rendering. No DataHub required."""

from agentlens.report import render_html


def _report(agents=None, skills=None, tools=None):
    return {
        "root": "urn:li:dataset:(urn:li:dataPlatform:snowflake,db.analytics.orders,PROD)",
        "agents": agents or [],
        "skills": skills or [],
        "tools": tools or [],
        "total_downstream": 5,
    }


def _agent(name="finance-copilot", hops=2):
    return {
        "urn": f"urn:li:dataset:(x,{name},PROD)",
        "name": name,
        "kind": "agent",
        "subtype": "AI Agent",
        "repository": "acme/agents",
        "source_path": "agentlens.yaml",
        "owner_team": "fpa",
        "hops": hops,
    }


def _skill(name="revenue-lookup", hops=1):
    return {
        "urn": f"urn:li:dataset:(x,{name},PROD)",
        "name": name,
        "kind": "skill",
        "subtype": "Agent Skill",
        "repository": "acme/agents",
        "source_path": f"skills/{name}/SKILL.md",
        "owner_team": "",
        "hops": hops,
    }


def test_renders_valid_html():
    out = render_html(_report([_agent()], [_skill()]), reason="dropping a column")
    assert out.startswith("<!DOCTYPE html>")
    assert out.rstrip().endswith("</html>")


def test_shows_agent_count():
    out = render_html(_report([_agent("a"), _agent("b")], [_skill()]))
    assert ">2<" in out


def test_empty_report_reads_as_safe():
    out = render_html(_report())
    assert "No agents read this asset" in out
    assert "safe" in out


def test_includes_names_and_teams():
    out = render_html(_report([_agent()], [_skill()]))
    assert "finance-copilot" in out
    assert "revenue-lookup" in out
    assert "fpa" in out


def test_includes_the_reason():
    out = render_html(_report([_agent()]), reason="dropping column discount_pct")
    assert "discount_pct" in out


def test_escapes_html_in_data():
    evil = _agent(name="<script>alert(1)</script>")
    out = render_html(_report([evil]))
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_renders_write_back_log():
    out = render_html(_report([_agent()]), actions=["tagged upstream `has-agent-consumers`"])
    assert "has-agent-consumers" in out
    assert "Written back" in out


def test_cascade_groups_by_hop():
    out = render_html(_report([_agent(hops=2)], [_skill(hops=1)]))
    assert "1 hop" in out
    assert "2 hops" in out


def test_is_self_contained():
    """No local assets - it must open from anywhere."""
    out = render_html(_report([_agent()]))
    assert "<style>" in out
    assert 'src="./' not in out and 'href="./' not in out


def test_pluralises_hops():
    """One hop is singular, more than one is plural."""
    out = render_html(_report([_agent(hops=2)], [_skill(hops=1)]))
    assert ">1 hop<" in out
    assert ">2 hops<" in out


def test_agent_source_path_is_shown():
    out = render_html(_report([_agent()]))
    assert "agentlens.yaml" in out
