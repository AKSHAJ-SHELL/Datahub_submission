"""Scanner tests. No DataHub required."""

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
        (d / "SKILL.md").write_text("# bare\n\nFROM analytics.things\n")
        skills = scan_skills(str(tmp_path), "r")
        assert len(skills) == 1
        assert skills[0].id == "bare"  # falls back to directory name

    def test_malformed_frontmatter_does_not_crash(self, tmp_path):
        d = tmp_path / "skills" / "broken"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\n: : not yaml : :\n---\n# broken\n")
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
        (d / "SKILL.md").write_text("# solo\n\nFROM analytics.x\n")
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
            (d / "SKILL.md").write_text("# hidden\n")
        assert scan(str(tmp_path), "r").skills == []

    def test_summary_is_readable(self, skill_repo):
        s = scan(str(skill_repo), "r").summary()
        assert "agent" in s and "skill" in s


class TestAliasBinding:
    """The fix for the 8/24 resolution rate: most of those 24 were columns."""

    def test_drops_aliased_columns(self):
        text = "SELECT o.created_at, o.line_total FROM analytics.orders o"
        raws = {r.raw.lower() for r in extract_data_refs(text, "x.md")}
        assert "analytics.orders" in raws
        assert "o.created_at" not in raws
        assert "o.line_total" not in raws

    def test_drops_aliases_from_joins(self):
        text = "FROM analytics.orders o JOIN analytics.products p ON p.product_id = o.product_id"
        raws = {r.raw.lower() for r in extract_data_refs(text, "x.md")}
        assert "analytics.orders" in raws
        assert "analytics.products" in raws
        assert "p.product_id" not in raws
        assert "o.product_id" not in raws

    def test_handles_as_keyword(self):
        text = "FROM analytics.orders AS o WHERE o.status = 'x'"
        raws = {r.raw.lower() for r in extract_data_refs(text, "x.md")}
        assert "o.status" not in raws

    def test_does_not_treat_keywords_as_aliases(self):
        """`FROM analytics.orders WHERE ...` must not bind `where`."""
        text = "FROM analytics.orders WHERE where.thing = 1"
        raws = {r.raw.lower() for r in extract_data_refs(text, "x.md")}
        assert "analytics.orders" in raws

    def test_unaliased_dotted_tokens_survive(self):
        text = "The agent reads warehouse.analytics.orders directly."
        raws = {r.raw.lower() for r in extract_data_refs(text, "x.md")}
        assert "warehouse.analytics.orders" in raws
