#!/usr/bin/env python3
"""
Two fixes found while reviewing the first HTML report.

Run from inside agentlens-project/:

    python patch_fixes.py
    pytest -v

1. Agents rendered "-" in the Source column. The scanner records source_path
   for agents but the emitter never wrote it into custom properties, so
   impact.py had nothing to read.

2. The distance pill said "2 hop". The cascade columns already pluralised;
   the table didn't.

Also adds a regression test for the pluralisation so it can't come back.
"""

import os
import sys


def patch(path: str, old: str, new: str, label: str) -> bool:
    if not os.path.exists(path):
        print(f"  MISS   {path} does not exist")
        return False
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if new in text and old not in text:
        print(f"  ok     {label} (already applied)")
        return True
    if old not in text:
        print(f"  MISS   {label} - source text not found in {path}")
        return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(old, new, 1))
    print(f"  patch  {label}")
    return True


def main() -> int:
    if not os.path.isdir("agentlens"):
        print("Run this from inside agentlens-project/.")
        return 1

    results = []

    # -- 1. agents carry their source path -------------------------------
    results.append(
        patch(
            "agentlens/emitter.py",
            """                    "agentlens.kind": "agent",
                    "agentlens.source_repository": agent.source_repository,
                    "agentlens.model": agent.model,""",
            """                    "agentlens.kind": "agent",
                    "agentlens.source_repository": agent.source_repository,
                    "agentlens.source_path": agent.source_path,
                    "agentlens.model": agent.model,""",
            "emitter: agents record source_path",
        )
    )

    # -- 2. pluralise the distance pill ----------------------------------
    results.append(
        patch(
            "agentlens/report.py",
            """            f'<td><span class="pill">{_e(item["hops"])} hop</span></td>'""",
            """            f'<td><span class="pill">{_e(item["hops"])} '
            f'hop{"s" if item["hops"] != 1 else ""}</span></td>'""",
            "report: pluralise the distance pill",
        )
    )

    # -- 3. regression test ----------------------------------------------
    test_path = "tests/test_report.py"
    marker = "def test_pluralises_hops"
    if os.path.exists(test_path):
        with open(test_path, encoding="utf-8") as fh:
            tests = fh.read()
        if marker in tests:
            print("  ok     tests: pluralisation test (already present)")
            results.append(True)
        else:
            tests += '''

def test_pluralises_hops():
    """One hop is singular, more than one is plural."""
    out = render_html(_report([_agent(hops=2)], [_skill(hops=1)]))
    assert ">1 hop<" in out
    assert ">2 hops<" in out


def test_agent_source_path_is_shown():
    out = render_html(_report([_agent()]))
    assert "agentlens.yaml" in out
'''
            with open(test_path, "w", encoding="utf-8") as fh:
                fh.write(tests)
            print("  patch  tests: added pluralisation + source path tests")
            results.append(True)

    print()
    if all(results):
        print("  All patches applied.\n")
        print("""Re-run to see the fixes, then check the Source column:

    python -m agentlens.cli scan demo-repo --repository github.com/acme/data-agents
    python -m agentlens.cli emit manifest.json
    python -m agentlens.cli guard \\
      "urn:li:dataset:(urn:li:dataPlatform:snowflake,b2fd91.order_entry_db.analytics.order_details,PROD)" \\
      --reason "dropping column discount_pct" \\
      --html examples/blast-radius.html \\
      --json examples/guard-run.json

    pytest -v
    open examples/blast-radius.html
""")
        return 0

    print("  Some patches did not apply - paste this output and I'll fix the match.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
