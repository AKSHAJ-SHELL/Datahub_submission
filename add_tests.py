#!/usr/bin/env python3
"""
Adds a test suite to AgentLens.

Run from inside agentlens-project/:

    python add_tests.py
    pip install pytest
    pytest -v

Most tests run with no DataHub and no network - a judge can clone the repo
and run pytest without standing up Docker. The integration tests skip
themselves automatically when GMS isn't reachable.
"""

import os
import sys

FILES = {}

FILES["tests/__init__.py"] = ""

# ===========================================================================
FILES["tests/conftest.py"] = '''import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GMS = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080").rstrip("/")


def _gms_up() -> bool:
    try:
        return requests.get(f"{GMS}/config", timeout=3).status_code == 200
    except Exception:
        return False


needs_gms = pytest.mark.skipif(not _gms_up(), reason="DataHub GMS not reachable")


@pytest.fixture
def skill_repo(tmp_path):
    """A minimal but realistic agent repo."""
    skills = tmp_path / "skills" / "sales-report"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        """---
name: sales-report
description: Daily sales rollup.
tools:
  - run_query
---

# sales-report

```sql
SELECT SUM(amount) FROM analytics.orders o
JOIN analytics.customers c ON c.id = o.customer_id
```

See os.path and https://docs.datahub.com for details. Ignore config.yaml.
"""
    )

    (tmp_path / ".mcp.json").write_text(
        """{"mcpServers": {"warehouse": {"command": "python", "tools": ["run_query"]}}}"""
    )

    (tmp_path / "agentlens.yaml").write_text(
        """agents:
  - id: sales-bot
    name: sales-bot
    owner_team: revops
    skills: [sales-report]
    tools: [run_query]
"""
    )
    return tmp_path
'''

# ===========================================================================
FILES["tests/test_scanner.py"] = '''"""Scanner tests. No DataHub required."""

from agentlens.scanner import extract_data_refs, scan, scan_mcp_configs, scan_skills


class TestExtractDataRefs:
    def test_finds_sql_tables_with_high_confidence(self):
        refs = extract_data_refs("SELECT * FROM analytics.orders", "x.md")
        found = {r.raw.lower(): r for r in refs}
        assert "analytics.orders" in found
        assert found["analytics.orders"].confidence >= 0.9

    def test_finds_join_targets(self):
        refs = extract_data_refs(
            "FROM analytics.orders o JOIN analytics.customers c ON c.id = o.cid", "x.md"
        )
        raws = {r.raw.lower() for r in refs}
        assert "analytics.orders" in raws
        assert "analytics.customers" in raws

    def test_handles_three_part_names(self):
        refs = extract_data_refs("FROM warehouse.analytics.orders", "x.md")
        assert any(r.raw.lower() == "warehouse.analytics.orders" for r in refs)

    def test_filters_module_paths(self):
        refs = extract_data_refs("import os.path and np.array here", "x.md")
        raws = {r.raw.lower() for r in refs}
        assert "os.path" not in raws
        assert "np.array" not in raws

    def test_filters_domains(self):
        refs = extract_data_refs("see https://docs.datahub.com for more", "x.md")
        assert not any("datahub.com" in r.raw.lower() for r in refs)

    def test_filters_filenames(self):
        refs = extract_data_refs("edit config.yaml and main.py", "x.md")
        raws = {r.raw.lower() for r in refs}
        assert "config.yaml" not in raws
        assert "main.py" not in raws

    def test_records_source_file(self):
        refs = extract_data_refs("FROM analytics.orders", "skills/a/SKILL.md")
        assert all(r.source_file == "skills/a/SKILL.md" for r in refs)

    def test_deduplicates(self):
        text = "FROM analytics.orders ... again FROM analytics.orders"
        refs = extract_data_refs(text, "x.md")
        assert len([r for r in refs if r.raw.lower() == "analytics.orders"]) == 1

    def test_empty_text_is_safe(self):
        assert extract_data_refs("", "x.md") == []


class TestScanSkills:
    def test_reads_frontmatter(self, skill_repo):
        skills = scan_skills(str(skill_repo), "acme/agents")
        assert len(skills) == 1
        assert skills[0].id == "sales-report"
        assert "Daily sales rollup" in skills[0].description

    def test_records_source_path(self, skill_repo):
        skill = scan_skills(str(skill_repo), "acme/agents")[0]
        assert skill.source_path.endswith("SKILL.md")
        assert skill.source_repository == "acme/agents"

    def test_hashes_instructions(self, skill_repo):
        skill = scan_skills(str(skill_repo), "acme/agents")[0]
        assert len(skill.instructions_sha) == 12

    def test_extracts_declared_tools(self, skill_repo):
        skill = scan_skills(str(skill_repo), "acme/agents")[0]
        assert "run_query" in skill.tools

    def test_finds_data_refs(self, skill_repo):
        skill = scan_skills(str(skill_repo), "acme/agents")[0]
        raws = {r.raw.lower() for r in skill.data_refs}
        assert "analytics.orders" in raws

    def test_skill_without_frontmatter(self, tmp_path):
        d = tmp_path / "skills" / "bare"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# bare\\n\\nFROM analytics.things\\n")
        skills = scan_skills(str(tmp_path), "r")
        assert len(skills) == 1
        assert skills[0].id == "bare"          # falls back to directory name

    def test_malformed_frontmatter_does_not_crash(self, tmp_path):
        d = tmp_path / "skills" / "broken"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\\n: : not yaml : :\\n---\\n# broken\\n")
        assert len(scan_skills(str(tmp_path), "r")) == 1

    def test_empty_repo(self, tmp_path):
        assert scan_skills(str(tmp_path), "r") == []


class TestScanMcp:
    def test_reads_declared_tools(self, skill_repo):
        tools = scan_mcp_configs(str(skill_repo))
        assert any(t.name == "run_query" and t.server == "warehouse" for t in tools)

    def test_server_without_tools_becomes_one_entry(self, tmp_path):
        (tmp_path / ".mcp.json").write_text('{"mcpServers": {"solo": {"command": "x"}}}')
        tools = scan_mcp_configs(str(tmp_path))
        assert len(tools) == 1
        assert tools[0].server == "solo"

    def test_malformed_json_is_skipped(self, tmp_path):
        (tmp_path / ".mcp.json").write_text("{not json")
        assert scan_mcp_configs(str(tmp_path)) == []

    def test_no_config(self, tmp_path):
        assert scan_mcp_configs(str(tmp_path)) == []


class TestScan:
    def test_full_scan(self, skill_repo):
        m = scan(str(skill_repo), "acme/agents")
        assert len(m.agents) == 1
        assert len(m.skills) == 1
        assert m.agents[0].id == "sales-bot"
        assert m.agents[0].owner_team == "revops"
        assert "sales-report" in m.agents[0].skills

    def test_infers_agent_without_yaml(self, tmp_path):
        d = tmp_path / "skills" / "solo"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# solo\\n\\nFROM analytics.x\\n")
        m = scan(str(tmp_path), "r")
        assert len(m.agents) == 1
        assert "solo" in m.agents[0].skills

    def test_empty_repo_yields_nothing(self, tmp_path):
        m = scan(str(tmp_path), "r")
        assert m.agents == [] and m.skills == []

    def test_ignores_git_and_venv(self, tmp_path):
        for junk in (".git", ".venv", "node_modules"):
            d = tmp_path / junk / "skills" / "hidden"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("# hidden\\n")
        assert scan(str(tmp_path), "r").skills == []

    def test_summary_is_readable(self, skill_repo):
        s = scan(str(skill_repo), "r").summary()
        assert "agent" in s and "skill" in s
'''

# ===========================================================================
FILES["tests/test_model.py"] = '''"""Manifest serialisation."""

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
'''

# ===========================================================================
FILES["tests/test_report.py"] = '''"""HTML report rendering. No DataHub required."""

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
    return {"urn": f"urn:li:dataset:(x,{name},PROD)", "name": name, "kind": "agent",
            "subtype": "AI Agent", "repository": "acme/agents",
            "source_path": "agentlens.yaml", "owner_team": "fpa", "hops": hops}


def _skill(name="revenue-lookup", hops=1):
    return {"urn": f"urn:li:dataset:(x,{name},PROD)", "name": name, "kind": "skill",
            "subtype": "Agent Skill", "repository": "acme/agents",
            "source_path": f"skills/{name}/SKILL.md", "owner_team": "", "hops": hops}


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
'''

# ===========================================================================
FILES["tests/test_integration.py"] = '''"""Integration tests. Skipped automatically when GMS is down."""

import os

import pytest

from tests.conftest import needs_gms

from agentlens.impact import blast_radius
from agentlens.resolver import Resolver


@needs_gms
class TestResolver:
    def test_resolves_a_known_table(self):
        r = Resolver()
        # Any dataset in the showcase pack.
        assert r.resolve("order_details") or r.resolve("customers")

    def test_unknown_token_returns_none(self):
        assert Resolver().resolve("definitely_not_a_real_table_xyz123") is None

    def test_caches(self):
        r = Resolver()
        r.resolve("order_details")
        assert "order_details" in r._cache


@needs_gms
class TestBlastRadius:
    def test_returns_expected_shape(self):
        report = blast_radius(
            "urn:li:dataset:(urn:li:dataPlatform:agentlens,agent.finance-copilot,PROD)"
        )
        for key in ("root", "agents", "skills", "tools", "total_downstream"):
            assert key in report

    def test_nonexistent_urn_is_empty_not_an_error(self):
        report = blast_radius("urn:li:dataset:(urn:li:dataPlatform:nope,nope.nope,PROD)")
        assert report["agents"] == []

    def test_terminal_node_has_no_downstream(self):
        """An agent is the end of the chain - nothing should be downstream of it."""
        report = blast_radius(
            "urn:li:dataset:(urn:li:dataPlatform:agentlens,agent.finance-copilot,PROD)"
        )
        assert report["agents"] == []

    def test_respects_hop_limit(self):
        urn = os.environ.get("AGENTLENS_TEST_ROOT")
        if not urn:
            pytest.skip("set AGENTLENS_TEST_ROOT to a table with agents downstream")
        shallow = blast_radius(urn, max_hops=1)
        deep = blast_radius(urn, max_hops=6)
        assert len(shallow["agents"]) <= len(deep["agents"])
'''

# ===========================================================================
FILES["pytest.ini"] = '''[pytest]
testpaths = tests
python_files = test_*.py
addopts = -q --tb=short
'''


def main() -> int:
    if not os.path.isdir("agentlens"):
        print("Run this from inside agentlens-project/.")
        return 1

    for rel, content in FILES.items():
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        with open(rel, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"  write  {rel}")

    print("""
    pip install pytest
    pytest -v

Unit tests need nothing. Integration tests skip themselves if GMS is down.

For the hop-limit test:
    export AGENTLENS_TEST_ROOT="urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.analytics.order_details,PROD)"
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
