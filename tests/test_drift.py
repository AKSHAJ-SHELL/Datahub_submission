"""Drift detection. Pure diff, so no DataHub and no network."""

from agentlens.drift import Change, compare, expected_state, render
from agentlens.impact import _urn_for
from agentlens.model import Agent, DataRef, Manifest, Skill, Tool

ORDERS = "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.orders,PROD)"
SUBS = "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.subscriptions,PROD)"

SKILL_URN = _urn_for("skill", "revenue-lookup")


def _manifest(sha="aaa111", refs=((("analytics.orders"), ORDERS),)):
    return Manifest(
        agents=[Agent(id="finance-copilot", name="finance-copilot", skills=["revenue-lookup"])],
        skills=[Skill(
            id="revenue-lookup", name="revenue-lookup", instructions_sha=sha,
            data_refs=[DataRef(raw=raw, source_file="SKILL.md", resolved_urn=urn)
                       for raw, urn in refs],
        )],
        tools=[Tool(name="run_query", server="warehouse", source_file=".mcp.json")],
    )


def _catalog(expected, sha="aaa111", upstreams=(ORDERS,)):
    """A catalog that agrees with the manifest, unless a test perturbs it."""
    out = {}
    for urn, want in expected.items():
        out[urn] = {
            "sha": sha if want["kind"] == "skill" else "",
            "upstreams": set(upstreams) if want["kind"] == "skill" else set(want["upstreams"]),
        }
    return out


def test_no_drift_when_catalog_matches():
    expected = expected_state(_manifest())
    assert compare(expected, _catalog(expected)) == []


def test_detects_changed_instructions():
    expected = expected_state(_manifest(sha="bbb222"))
    changes = compare(expected, _catalog(expected, sha="aaa111"))
    assert [c.kind for c in changes] == ["changed"]
    assert "aaa111 -> bbb222" in changes[0].detail


def test_detects_a_newly_read_table():
    expected = expected_state(_manifest(
        refs=(("analytics.orders", ORDERS), ("analytics.subscriptions", SUBS)),
    ))
    changes = compare(expected, _catalog(expected, upstreams=(ORDERS,)))
    assert [c.kind for c in changes] == ["ref-added"]
    assert "analytics.subscriptions" in changes[0].detail


def test_detects_a_table_no_longer_read():
    expected = expected_state(_manifest())
    changes = compare(expected, _catalog(expected, upstreams=(ORDERS, SUBS)))
    assert [c.kind for c in changes] == ["ref-removed"]
    assert "analytics.subscriptions" in changes[0].detail


def test_unresolved_reference_is_reported_as_broken():
    expected = expected_state(_manifest(refs=(("analytics.events", None),)))
    changes = compare(expected, _catalog(expected, upstreams=()))
    kinds = [c.kind for c in changes]
    assert "broken-ref" in kinds
    assert any("analytics.events" in c.detail for c in changes)


def test_detects_a_node_that_was_never_emitted():
    expected = expected_state(_manifest())
    catalog = _catalog(expected)
    catalog[SKILL_URN] = None
    changes = compare(expected, catalog)
    assert [c.kind for c in changes] == ["new"]


def test_detects_a_node_whose_source_is_gone():
    expected = expected_state(_manifest())
    stale = _urn_for("skill", "deleted-skill")
    changes = compare(expected, _catalog(expected), catalog_urns=list(expected) + [stale])
    assert [c.kind for c in changes] == ["gone"]


def test_broken_refs_sort_above_everything_else():
    changes = [
        Change("new", "z", "urn:z", ""),
        Change("broken-ref", "a", "urn:a", "gone"),
        Change("changed", "m", "urn:m", ""),
    ]
    assert [c.kind for c in sorted(
        changes, key=lambda c: __import__("agentlens.drift", fromlist=["SEVERITY"]).SEVERITY.index(c.kind)
    )][0] == "broken-ref"


def test_render_is_explicit_when_clean():
    out = render([], scanned=9)
    assert "No drift" in out
    assert "9 node(s)" in out


def test_render_explains_broken_references():
    out = render([Change("broken-ref", "ownership-audit", "urn:x",
                         "analytics.events no longer resolves")], scanned=9)
    assert "governance finding" in out
    assert "ownership-audit" in out
