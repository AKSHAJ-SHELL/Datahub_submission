import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GMS = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080").rstrip("/")


def _gms_up() -> bool:
    try:
        return requests.get(f"{GMS}/config", timeout=3).status_code == 200
    except requests.RequestException:
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
