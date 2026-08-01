"""The explorer payload. No browser needed - we assert on what gets embedded."""

import json

from agentlens.explorer import build_payload, discover_columns, render_html
from agentlens.impact import _urn_for
from agentlens.model import Agent, DataRef, Manifest, Skill
from agentlens.sandbox import fork

ORDERS = "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.order_details,PROD)"


def _manifest():
    return Manifest(
        repository="github.com/acme/data-agents",
        agents=[Agent(id="finance-copilot", name="finance-copilot",
                      owner_team="fpa-platform", skills=["margin-analysis"])],
        skills=[Skill(id="margin-analysis", name="margin-analysis",
                      data_refs=[DataRef(raw="analytics.order_details",
                                         source_file="SKILL.md", resolved_urn=ORDERS),
                                 DataRef(raw="analytics.events",
                                         source_file="SKILL.md", resolved_urn=None)])],
    )


def test_discovers_columns_from_qualified_references():
    fleet = fork(_manifest())
    fleet.skill_text = {"margin-analysis": "SELECT o.line_total, o.quantity FROM analytics.orders o"}
    assert discover_columns(fleet) == ["line_total", "quantity"]


def test_payload_carries_everything_the_page_simulates_with():
    payload = build_payload(_manifest(), "")
    assert {"index", "meta", "skillText", "agentSkills", "tables"} <= set(payload)
    assert ORDERS in payload["tables"]
    assert payload["tables"][ORDERS]["readers"] == ["margin-analysis"]
    assert payload["counts"] == {"agents": 1, "skills": 1, "tools": 0}


def test_unresolved_references_are_surfaced_not_dropped():
    assert build_payload(_manifest(), "")["unresolved"] == ["analytics.events"]


def test_page_is_self_contained_and_embeds_valid_json():
    page = render_html(build_payload(_manifest(), ""))
    assert page.startswith("<!DOCTYPE html>")
    assert "src=" not in page          # no external scripts, styles or images
    blob = page.split('type="application/json">')[1].split("</script>")[0]
    assert json.loads(blob.replace("<\\/", "</"))["tables"]


def test_the_agent_urn_is_the_one_the_emitter_would_mint():
    payload = build_payload(_manifest(), "")
    assert _urn_for("agent", "finance-copilot") in payload["meta"]
