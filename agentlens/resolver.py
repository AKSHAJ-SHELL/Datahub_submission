"""Resolve raw table tokens found in skills to real DataHub URNs.

Matching is scored rather than first-hit. A token like `analytics.orders`
should prefer a dataset actually named `...analytics.orders` over one that
merely contains the string somewhere in its URN - platform and environment
segments are part of a URN too, and substring matching across them produces
confident nonsense.
"""

from __future__ import annotations

import logging
import os
import re

import requests

GMS = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080").rstrip("/")
TOKEN = os.environ.get("DATAHUB_GMS_TOKEN", "")

logger = logging.getLogger(__name__)

HEADERS = {"Content-Type": "application/json"}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"

SEARCH = """
query($input: SearchAcrossEntitiesInput!) {
  searchAcrossEntities(input: $input) {
    searchResults { entity { urn ... on Dataset { name } } }
  }
}
"""

# urn:li:dataset:(urn:li:dataPlatform:<platform>,<name>,<env>)
URN_NAME = re.compile(r"^urn:li:dataset:\(urn:li:dataPlatform:[^,]+,(.+),[^,)]+\)$")

MIN_SCORE = 50


def dataset_name(urn: str) -> str:
    """The name segment of a dataset URN, or the URN if it doesn't parse."""
    match = URN_NAME.match(urn)
    return match.group(1) if match else urn


def score(token: str, name: str) -> int:
    """How well does a catalog dataset name match a token found in a skill?

    100  exact                     analytics.orders == analytics.orders
     90  suffix                    db.analytics.orders endswith .analytics.orders
     70  leaf                      trailing segment matches
     50  substring                 appears somewhere in the name
      0  no match
    """
    token, name = token.lower(), name.lower()
    if token == name:
        return 100
    if name.endswith("." + token):
        return 90
    if token.split(".")[-1] == name.split(".")[-1]:
        return 70
    if token in name:
        return 50
    return 0


class Resolver:
    """Looks tokens up in DataHub, with a cache so we hit the API once per token."""

    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}
        self._warned = False

    def _search(self, query: str, count: int = 20) -> list[dict]:
        try:
            resp = requests.post(
                f"{GMS}/api/graphql",
                headers=HEADERS,
                json={
                    "query": SEARCH,
                    "variables": {
                        "input": {
                            "types": ["DATASET"],
                            "query": query,
                            "start": 0,
                            "count": count,
                        }
                    },
                },
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            if not self._warned:
                self._warned = True
                logger.error(
                    "DataHub unreachable at %s (%s) - resolution returns nothing",
                    GMS,
                    exc,
                )
            return []
        body = resp.json()
        if "errors" in body:
            return []
        return body.get("data", {}).get("searchAcrossEntities", {}).get("searchResults", [])

    def resolve(self, token: str) -> str | None:
        """Best matching dataset URN for a token, or None.

        Searches the full token first, then progressively shorter suffixes -
        `db.schema.table` then `schema.table` then `table` - and keeps the
        highest-scoring candidate found at the first level that produces one.
        """
        key = token.lower()
        if key in self._cache:
            return self._cache[key]

        result: str | None = None
        parts = token.split(".")
        attempts = [".".join(parts[i:]) for i in range(len(parts))]

        for attempt in attempts:
            if len(attempt) < 3:
                continue

            best_urn, best_score = None, 0
            for hit in self._search(attempt):
                urn = hit["entity"]["urn"]
                if "dataPlatform:agentlens" in urn:
                    continue
                name = hit["entity"].get("name") or dataset_name(urn)
                hit_score = max(score(token, name), score(attempt, name))
                if hit_score > best_score:
                    best_urn, best_score = urn, hit_score

            if best_urn is not None and best_score >= MIN_SCORE:
                result = best_urn
                break

        self._cache[key] = result
        return result

    def resolve_manifest(self, manifest) -> tuple[int, int]:
        """Resolve every data ref in place. Returns (resolved, total)."""
        resolved = total = 0
        for skill in manifest.skills:
            for ref in skill.data_refs:
                total += 1
                urn = self.resolve(ref.raw)
                if urn:
                    ref.resolved_urn = urn
                    resolved += 1
        return resolved, total
