"""Resolve raw table tokens found in skills to real DataHub URNs."""

from __future__ import annotations

import os

import requests

GMS = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080").rstrip("/")
TOKEN = os.environ.get("DATAHUB_GMS_TOKEN", "")

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


class Resolver:
    """Looks tokens up in DataHub, with a cache so we hit the API once per token."""

    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}

    def _search(self, query: str, count: int = 10) -> list[dict]:
        resp = requests.post(
            f"{GMS}/api/graphql",
            headers=HEADERS,
            json={
                "query": SEARCH,
                "variables": {
                    "input": {"types": ["DATASET"], "query": query, "start": 0, "count": count}
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            return []
        return body.get("data", {}).get("searchAcrossEntities", {}).get("searchResults", [])

    def resolve(self, token: str) -> str | None:
        """Return the best matching dataset URN for a token, or None."""
        key = token.lower()
        if key in self._cache:
            return self._cache[key]

        result = None
        # Try the full token, then progressively shorter suffixes:
        # "db.schema.table" -> "schema.table" -> "table"
        parts = token.split(".")
        attempts = [".".join(parts[i:]) for i in range(len(parts))]

        for attempt in attempts:
            if len(attempt) < 3:
                continue
            hits = self._search(attempt)
            for hit in hits:
                urn = hit["entity"]["urn"]
                if "agentlens" in urn:
                    continue
                if attempt.lower() in urn.lower():
                    result = urn
                    break
            if result:
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
