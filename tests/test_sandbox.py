"""Sandbox simulation. Pure - no DataHub, no repo, no network."""

import pytest

from agentlens.model import Agent, DataRef, Manifest, Skill
from agentlens.sandbox import (
    BREAKS,
    DEGRADES,
    UNCHANGED,
    Change,
    Fleet,
    fork,
    mentions_column,
    render,
    resolve_table,
    simulate,
)

ORDERS = "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.order_details,PROD)"


def _manifest():
    """Two skills read the same table. Only one names the column."""
    return Manifest(
        agents=[
            Agent(id="finance-copilot", name="finance-copilot", owner_team="fpa-platform",
                  skills=["margin-analysis"]),
            Agent(id="growth-analyst", name="growth-analyst", owner_team="growth-eng",
                  skills=["churn-risk"]),
        ],
        skills=[
            Skill(id="margin-analysis", name="margin-analysis",
                  data_refs=[DataRef(raw="analytics.order_details",
                                     source_file="SKILL.md", resolved_urn=ORDERS)]),
            Skill(id="churn-risk", name="churn-risk",
                  data_refs=[DataRef(raw="analytics.order_details",
                                     source_file="SKILL.md", resolved_urn=ORDERS)]),
        ],
    )


def _fleet(margin_text, churn_text):
    f = fork(_manifest())
    f.skill_text = {"margin-analysis": margin_text, "churn-risk": churn_text}
    return f


# -- the distinction the graph cannot make -----------------------------------

def test_only_skills_naming_the_column_break():
    fleet = _fleet(
        "SELECT SUM(o.discount_pct) FROM analytics.order_details o",
        "SELECT customer_id FROM analytics.order_details",
    )
    report = simulate(fleet, Change("drop-column", ORDERS, column="discount_pct"))
    by_name = {e["name"]: e for e in report["effects"]}
    assert by_name["margin-analysis"]["severity"] == BREAKS
    assert by_name["churn-risk"]["severity"] == UNCHANGED
    assert report["counts"] == {BREAKS: 1, DEGRADES: 1, UNCHANGED: 2}


def test_the_agent_above_a_broken_skill_degrades_and_names_the_route():
    fleet = _fleet("uses discount_pct", "no mention")
    report = simulate(fleet, Change("drop-column", ORDERS, column="discount_pct"))
    by_name = {e["name"]: e for e in report["effects"]}
    assert by_name["finance-copilot"]["severity"] == DEGRADES
    assert "margin-analysis" in by_name["finance-copilot"]["why"]
    assert by_name["finance-copilot"]["owner_team"] == "fpa-platform"
    assert by_name["growth-analyst"]["severity"] == UNCHANGED


def test_dropping_the_table_breaks_everything_downstream():
    fleet = _fleet("no mention at all", "none either")
    report = simulate(fleet, Change("drop-table", ORDERS))
    assert report["counts"][BREAKS] == 2
    assert report["counts"][UNCHANGED] == 0


def test_renaming_breaks_every_reader_until_its_text_changes():
    fleet = _fleet("x", "y")
    report = simulate(fleet, Change("rename-table", ORDERS, new_name="analytics.orders_v2"))
    assert report["counts"][BREAKS] == 2
    assert all("orders_v2" in e["why"] for e in report["effects"] if e["kind"] == "skill")


# -- never claim safety you cannot back up -----------------------------------

def test_an_unreadable_skill_counts_as_affected_not_as_safe():
    fleet = _fleet("", "SELECT 1")
    report = simulate(fleet, Change("drop-column", ORDERS, column="discount_pct"))
    by_name = {e["name"]: e for e in report["effects"]}
    assert by_name["margin-analysis"]["severity"] == BREAKS
    assert "could not read" in by_name["margin-analysis"]["why"]
    assert report["warnings"]


# -- column matching ---------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("SUM(discount_pct)", True),
    ("o.discount_pct", True),
    ("DISCOUNT_PCT", True),
    ("discount_pct_v2", False),
    ("net_discount_pct", False),
    ("", False),
])
def test_column_matching_is_word_bounded(text, expected):
    assert mentions_column(text, "discount_pct") is expected


# -- plumbing ----------------------------------------------------------------

def test_nothing_is_written_by_a_simulation():
    report = simulate(_fleet("a", "b"), Change("drop-table", ORDERS))
    assert report["written"] is False
    assert "nothing below has been written" in render(report).lower()
    assert "Nothing was written" in render(report)


def test_a_promoting_run_does_not_claim_nothing_was_written():
    report = simulate(_fleet("discount_pct", "no"),
                      Change("drop-column", ORDERS, column="discount_pct"))
    report["written"] = True
    out = render(report)
    assert "promoting this finding" in out
    assert "Nothing was written" not in out


def test_report_is_shaped_for_the_write_back_actions():
    report = simulate(_fleet("discount_pct", "no"),
                      Change("drop-column", ORDERS, column="discount_pct"))
    assert {"root", "agents", "skills", "tools"} <= set(report)
    assert report["agents"][0]["owner_team"] == "fpa-platform"
    assert all("hops" in a for a in report["agents"])


def test_a_table_with_nothing_downstream_says_so():
    report = simulate(Fleet(), Change("drop-table", "urn:li:dataset:(x,y,PROD)"))
    assert report["effects"] == []
    assert "Nothing downstream" in render(report)


def test_resolve_table_accepts_a_fragment_and_refuses_an_ambiguous_one():
    m = _manifest()
    assert resolve_table(m, "order_details") == ORDERS
    assert resolve_table(m, ORDERS) == ORDERS
    assert resolve_table(m, "nope") is None


def test_render_explains_why_lineage_alone_would_over_report():
    report = simulate(_fleet("discount_pct", "no mention"),
                      Change("drop-column", ORDERS, column="discount_pct"))
    assert "Lineage alone would have" in render(report)
