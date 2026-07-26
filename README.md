# AgentLens

**Lineage for your AI agents.**

Your dashboards have lineage. Your agents don't. When someone drops a column,
you can see which Looker dashboard breaks - you have no idea which of your
agents quietly starts hallucinating.

AgentLens scans your agent repositories, catalogues every agent, skill, and MCP
tool into DataHub as first-class assets with real lineage back to the warehouse
tables they read, and then answers the question nobody can answer today:

> If I change this table, which agents degrade?

---

## Quickstart

```bash
pip install -r requirements.txt

export DATAHUB_GMS_URL=http://localhost:8080
export DATAHUB_GMS_TOKEN=your-token

python -m agentlens.cli scan demo-repo --repository github.com/acme/data-agents
python -m agentlens.cli emit manifest.json
python -m agentlens.cli impact "urn:li:dataset:(urn:li:dataPlatform:snowflake,...,PROD)"
```

## How it works

```
   repo scan          URN resolution         emit                traversal
  ----------         ----------------      --------            ------------
  .mcp.json    -->   match table refs -->  datasets in    -->   downstream
  SKILL.md           against DataHub       platform=            walk finds
  agentlens.yaml     search                agentlens            agents
```

## Why agents are modelled as datasets

DataHub defines native `aiAgent`, `agentSkill`, and `api` entity types in
`entity-registry.yml` on `master`. They are **not** in the v1.6.0 entity
registry and have no Python SDK classes. Emitting one returns:

```
400 {"error":"Failed to find entity with name aiAgent in EntityRegistry"}
```

Verified against both v1.5.0.6 and v1.6.0 - see `probe_v2.py`.

So AgentLens models agents and skills as datasets in a dedicated `agentlens`
platform, distinguished by subtype, with agent metadata in custom properties.
This is a standard DataHub pattern for non-table assets. Because
`upstreamLineage` is dataset-to-dataset, every traversal is identical to what
it would be against native entities - when those ship, only `emitter.py`
changes.

## License

Apache 2.0.
