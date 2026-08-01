#!/usr/bin/env python3
"""
AgentLens probe v2 - fixes the registry parsing and tests emits empirically.

v1's section 4 was wrong (it failed its own controls). This version dumps the
raw registry response so we can see its actual shape, and decides everything
by trying real emits instead of guessing from JSON structure.

Usage:
    export DATAHUB_GMS_URL=http://localhost:8080
    export DATAHUB_GMS_TOKEN=dummy
    python probe_v2.py
"""

from __future__ import annotations

import os
import time

import requests

GMS = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080").rstrip("/")
TOKEN = os.environ.get("DATAHUB_GMS_TOKEN", "")

HEADERS = {"Content-Type": "application/json"}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"


def line(char="=", n=72):
    print(char * n)


# ---------------------------------------------------------------------------
line()
print("GMS VERSION")
line()
try:
    cfg = requests.get(f"{GMS}/config", headers=HEADERS, timeout=10).json()
    ver = cfg.get("versions", {}).get("acryldata/datahub", {}).get("version", "?")
    print(f"  {ver}")
except Exception as exc:
    print(f"  could not read /config: {exc}")
    raise SystemExit(1) from exc

# ---------------------------------------------------------------------------
line()
print("RAW REGISTRY RESPONSE (first 1500 chars)")
line()
try:
    resp = requests.get(
        f"{GMS}/openapi/v1/registry/models/entity/specifications",
        headers=HEADERS,
        timeout=30,
    )
    raw = resp.text
    print(f"  status {resp.status_code}, {len(raw)} bytes")
    print("  " + raw[:1500].replace("\n", "\n  "))
    with open("registry_raw.json", "w") as fh:
        fh.write(raw)
    print("\n  Full response saved to registry_raw.json")
except Exception as exc:
    print(f"  failed: {exc}")

# ---------------------------------------------------------------------------
line()
print("EMPIRICAL EMIT TEST - the only answer that matters")
line()

now = int(time.time() * 1000)
audit = {"time": now, "actor": "urn:li:corpuser:datahub"}

# entity path -> (urn, aspect name, aspect value)
CANDIDATES = {
    "dataset": (  # control - must pass
        "urn:li:dataset:(urn:li:dataPlatform:snowflake,agentlens.probe,PROD)",
        "datasetProperties",
        {"name": "agentlens probe", "description": "delete me"},
    ),
    "aiagent": (
        "urn:li:aiAgent:agentlens-probe",
        "aiAgentInfo",
        {
            "name": "AgentLens Probe",
            "source": "EXTERNAL",
            "created": audit,
            "lastModified": audit,
        },
    ),
    "agentskill": (
        "urn:li:agentSkill:agentlens-probe",
        "agentSkillInfo",
        {"name": "AgentLens Probe Skill", "created": audit, "lastModified": audit},
    ),
    "api": (
        "urn:li:api:agentlens-probe",
        "apiInfo",
        {"name": "AgentLens Probe API"},
    ),
    "application": (
        "urn:li:application:agentlens-probe",
        "applicationProperties",
        {"name": "AgentLens Probe"},
    ),
    "metric": (
        "urn:li:metric:agentlens-probe",
        "metricProperties",
        {"name": "AgentLens Probe"},
    ),
}

supported = []
unsupported = []

for path, (urn, aspect_name, aspect_value) in CANDIDATES.items():
    body = [{"urn": urn, aspect_name: {"value": aspect_value}}]
    try:
        r = requests.post(f"{GMS}/openapi/v3/entity/{path}", headers=HEADERS, json=body, timeout=20)
        if r.status_code < 300:
            print(f"  [ SUPPORTED ] {path:14s} -> {r.status_code}")
            supported.append((path, urn))
        else:
            msg = r.text[:160]
            print(f"  [    no     ] {path:14s} -> {r.status_code} {msg}")
            unsupported.append(path)
    except Exception as exc:
        print(f"  [   error   ] {path:14s} -> {exc}")
        unsupported.append(path)

# ---------------------------------------------------------------------------
line()
print("VERDICT")
line()

if "aiagent" in [s[0] for s in supported]:
    print("""
  GREEN - aiAgent works. Build AgentLens as planned.
""")
elif "dataset" not in [s[0] for s in supported]:
    print("""
  INCONCLUSIVE - even the dataset control failed, so something is wrong with
  auth or the endpoint rather than with entity support. Check the status codes
  above before drawing any conclusion.
""")
else:
    print("""
  RED - aiAgent is not in this build, but the emit path itself works
  (dataset succeeded).

  Next: try upgrading GMS.

      datahub docker quickstart --stop
      datahub docker quickstart --version v1.6.0
      python probe_v2.py

  If v1.6.0 still says no, STOP CHASING IT. The entity model is on master and
  unreleased. Go to Plan B - it costs you nothing in originality.
""")

print("  Clean up anything that got created:")
for _path, urn in supported:
    print(f"    datahub delete --urn '{urn}' --hard -f")
print()
