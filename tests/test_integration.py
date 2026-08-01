"""Integration tests. Skipped automatically when GMS is down."""

import os

import pytest

from agentlens.impact import blast_radius
from agentlens.resolver import Resolver
from tests.conftest import needs_gms


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
