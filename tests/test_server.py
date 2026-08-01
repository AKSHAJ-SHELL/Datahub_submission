"""Link merging and URL building. No server, no DataHub."""

from agentlens.model import DataRef, Manifest, Skill
from agentlens.server import LINK_DESCRIPTION, explorer_url, merge_link

URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.order_details,PROD)"


def _one_table_manifest():
    return Manifest(skills=[Skill(id="s", name="s", data_refs=[
        DataRef(raw="analytics.order_details", source_file="SKILL.md", resolved_urn=URN)])])
URL = "http://localhost:8000/?table=" + URN.replace(":", "%3A").replace(
    "(", "%28").replace(")", "%29").replace(",", "%2C")


def test_url_is_deep_linked_and_fully_escaped():
    url = explorer_url("http://localhost:8000", URN)
    assert url.startswith("http://localhost:8000/?table=")
    assert "(" not in url and "," not in url      # the urn must survive the query string


def test_trailing_slash_on_the_base_url_does_not_double_up():
    assert "//?table" not in explorer_url("http://localhost:8000/", URN)


def test_adds_our_link_to_an_empty_sidebar():
    elements, changed = merge_link([], "http://x/")
    assert changed
    assert elements == [{"url": "http://x/", "description": LINK_DESCRIPTION}]


def test_never_drops_someone_elses_link():
    theirs = {"url": "http://runbook/", "description": "On-call runbook"}
    elements, changed = merge_link([theirs], "http://x/")
    assert changed
    assert theirs in elements
    assert len(elements) == 2


def test_re_running_unchanged_is_a_no_op():
    first, _ = merge_link([], "http://x/")
    second, changed = merge_link(first, "http://x/")
    assert not changed
    assert second == first


def test_an_unreadable_sidebar_is_never_merged_into():
    """institutionalMemory is full-replace: writing from a failed read deletes links."""
    from agentlens import server

    def boom(_urn):
        return None

    original, server.existing_links = server.existing_links, boom
    try:
        rows = server.link_tables(_one_table_manifest(), "http://localhost:8000", dry_run=True)
    finally:
        server.existing_links = original
    assert [r["status"] for r in rows] == ["unknown"]
    assert "refusing to overwrite" in rows[0]["note"]


def test_a_new_port_updates_in_place_rather_than_accumulating():
    first, _ = merge_link([], "http://localhost:8000/")
    second, changed = merge_link(first, "http://localhost:9999/")
    assert changed
    assert len(second) == 1
    assert second[0]["url"] == "http://localhost:9999/"
