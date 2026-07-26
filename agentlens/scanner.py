"""Scan a repository for agents, skills, MCP tools, and the data they touch.

Scope is deliberately narrow. We parse:

  * .mcp.json / mcp.json / .claude/mcp.json   -> MCP servers and their tools
  * **/SKILL.md                               -> skills (YAML frontmatter + body)
  * agentlens.yaml                            -> explicit agent declarations
  * SQL and table-shaped tokens in skill text -> data references

We do NOT try to statically analyse arbitrary Python. That is a research
project, not a hackathon feature, and the failure mode is silent wrongness.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

import yaml

from .model import Agent, DataRef, Manifest, Skill, Tool

# FROM / JOIN / UPDATE / INTO followed by a table-ish token
SQL_TABLE = re.compile(
    r"\b(?:from|join|into|update)\s+[`\"\[]?([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*){1,3})",
    re.IGNORECASE,
)

# Bare dotted identifiers - schema.table or db.schema.table
DOTTED = re.compile(r"\b([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*){1,3})\b")

# Things that look dotted but are not tables
NOISE = {
    "e.g", "i.e", "etc.al", "self.name", "os.path", "np.array", "pd.read_csv",
    "datahub.com", "github.com", "docs.datahub.com", "www.example.com",
}
NOISE_PREFIXES = ("http", "www", "self.", "os.", "np.", "pd.", "json.", "yaml.")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from a markdown body."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except Exception:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), parts[2]


def extract_data_refs(text: str, source_file: str) -> list[DataRef]:
    """Pull probable table references out of prose or SQL."""
    refs: dict[str, DataRef] = {}

    for match in SQL_TABLE.finditer(text):
        token = match.group(1)
        refs[token.lower()] = DataRef(raw=token, source_file=source_file, confidence=0.9)

    for match in DOTTED.finditer(text):
        token = match.group(1)
        low = token.lower()
        if low in refs:
            continue
        if low in NOISE or any(low.startswith(p) for p in NOISE_PREFIXES):
            continue
        if token.endswith((".md", ".py", ".json", ".yaml", ".yml", ".sql", ".txt")):
            continue
        refs[low] = DataRef(raw=token, source_file=source_file, confidence=0.4)

    return list(refs.values())


def scan_mcp_configs(root: str) -> list[Tool]:
    tools: list[Tool] = []
    candidates = ["mcp.json", ".mcp.json", os.path.join(".claude", "mcp.json")]
    seen = set()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", ".venv", "__pycache__"}]
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if name not in {"mcp.json", ".mcp.json"} and rel not in candidates:
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path) as fh:
                    data = json.load(fh)
            except Exception:
                continue
            servers = data.get("mcpServers") or data.get("servers") or {}
            for server_name, server_cfg in servers.items():
                declared = []
                if isinstance(server_cfg, dict):
                    declared = server_cfg.get("tools") or []
                if declared:
                    for tool_name in declared:
                        key = (server_name, tool_name)
                        if key not in seen:
                            seen.add(key)
                            tools.append(Tool(name=tool_name, server=server_name, source_file=rel))
                else:
                    key = (server_name, server_name)
                    if key not in seen:
                        seen.add(key)
                        tools.append(Tool(name=server_name, server=server_name, source_file=rel))
    return tools


def scan_skills(root: str, repository: str) -> list[Skill]:
    skills: list[Skill] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", ".venv", "__pycache__"}]
        for name in filenames:
            if name.upper() != "SKILL.MD":
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue

            meta, body = _parse_frontmatter(text)
            skill_id = str(meta.get("name") or os.path.basename(dirpath))
            skills.append(
                Skill(
                    id=skill_id,
                    name=skill_id,
                    description=str(meta.get("description", ""))[:800],
                    source_repository=repository,
                    source_path=rel,
                    instructions_sha=_sha(body),
                    data_refs=extract_data_refs(body, rel),
                    tools=list(meta.get("tools") or meta.get("allowed-tools") or []),
                )
            )
    return skills


def scan_agents(root: str, repository: str, known_skills: list[str]) -> list[Agent]:
    """Read agentlens.yaml if present; otherwise infer one agent per repo."""
    agents: list[Agent] = []
    for candidate in ("agentlens.yaml", "agentlens.yml", os.path.join("agents", "agentlens.yaml")):
        path = os.path.join(root, candidate)
        if not os.path.exists(path):
            continue
        try:
            with open(path) as fh:
                data = yaml.safe_load(fh) or {}
        except Exception:
            continue
        for entry in data.get("agents", []):
            agents.append(
                Agent(
                    id=entry["id"],
                    name=entry.get("name", entry["id"]),
                    description=entry.get("description", ""),
                    source_repository=repository,
                    source_path=candidate,
                    model=entry.get("model", ""),
                    owner_team=entry.get("owner_team", ""),
                    skills=entry.get("skills", []),
                    tools=entry.get("tools", []),
                )
            )
        break

    if not agents and known_skills:
        agents.append(
            Agent(
                id=os.path.basename(os.path.abspath(root)),
                name=os.path.basename(os.path.abspath(root)),
                description="Inferred from repository layout (no agentlens.yaml found).",
                source_repository=repository,
                skills=known_skills,
            )
        )
    return agents


def scan(root: str, repository: str = "") -> Manifest:
    repository = repository or os.path.basename(os.path.abspath(root))
    tools = scan_mcp_configs(root)
    skills = scan_skills(root, repository)
    agents = scan_agents(root, repository, [s.id for s in skills])
    return Manifest(agents=agents, skills=skills, tools=tools, repository=repository)
