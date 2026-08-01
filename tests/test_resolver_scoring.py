"""Scoring logic. No DataHub required."""

from agentlens.resolver import dataset_name, score

URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.db.analytics.orders,PROD)"


def test_parses_name_out_of_urn():
    assert dataset_name(URN) == "b2fd91.db.analytics.orders"


def test_unparseable_urn_returns_itself():
    assert dataset_name("not-a-urn") == "not-a-urn"


def test_exact_match_scores_highest():
    assert score("analytics.orders", "analytics.orders") == 100


def test_suffix_beats_leaf():
    assert score("analytics.orders", "db.analytics.orders") == 90
    assert score("sales.orders", "db.analytics.orders") == 70


def test_no_match_scores_zero():
    assert score("analytics.customers", "db.analytics.orders") == 0


def test_scoring_is_case_insensitive():
    assert score("Analytics.Orders", "analytics.orders") == 100


def test_prefers_the_right_candidate():
    """A real ranking: order_details should not win for a query about orders."""
    candidates = ["db.analytics.order_details", "db.analytics.orders"]
    best = max(candidates, key=lambda n: score("analytics.orders", n))
    assert best == "db.analytics.orders"
