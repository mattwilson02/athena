# Athena — Project Instructions

> See [README.md](README.md) for project overview. See [SOUL.md](SOUL.md) for Athena's personality. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for technical deep-dive. See [docs/MCP_SPEC.md](docs/MCP_SPEC.md) for MCP server design.

## Tech Stack

| Layer | Tech |
|-------|------|
| Data store | Markdown files + YAML frontmatter in `vault/` |
| Schema | `vault/_meta/schema.md` — single source of truth, parsed at boot |
| Graph engine | NetworkX (Python) — in-memory directed graph |
| Vector search | ChromaDB (local) — semantic similarity |
| MCP server | Python (FastMCP) — 17 tools, zero API calls |
| AI reasoning | Claude via subscription (Desktop, Code, or claude.ai) |

## Directory Structure

```
athena/
├── vault/                        # Knowledge graph (markdown, git-tracked)
│   ├── Self/Goals/Fears/Beliefs/Values/Habits/Skills/
│   ├── People/Persons/Organisations/
│   ├── Knowledge/Books/Articles/Ideas/Notes/Movies/Quotes/Pills/
│   ├── Life/Experiences/Daily/Memories/
│   ├── Planning/Tasks/Projects/Reminders/Events/
│   ├── Places/
│   ├── Finance/Expenses/Subscriptions/Budgets/
│   ├── _meta/schema.md           # Executable schema — domains, types, edges
│   ├── _templates/               # Node file templates
│   └── _backup/                  # Archived nodes (skipped by parser)
├── backend/
│   ├── mcp_server.py             # MCP server entry point (FastMCP, 17 tools)
│   ├── permanence.py             # Node permanence levels and scoring
│   ├── vault_parser.py           # Markdown → nodes + edges
│   ├── vault_graph.py            # NetworkX graph wrapper
│   ├── schema_parser.py          # Parses schema.md at boot
│   ├── vector_search.py          # ChromaDB semantic search
│   ├── services/
│   │   ├── vault_service.py      # File I/O, cross-referencing, repair (thread-safe)
│   │   ├── conflict_service.py   # Contradiction detection
│   │   ├── accountability_service.py # Streaks, commitments, fundamentals
│   │   ├── relationship_service.py   # Person mention tracking, health
│   │   ├── state_service.py      # User state inference
│   │   └── audit_service.py      # Vault structural health
│   ├── middleware/
│   │   └── security.py           # Path traversal prevention
│   ├── tests/                    # pytest suite
│   ├── requirements.txt
│   └── .env                      # VAULT_PATH only
├── archive/                      # V1 code (Flask, Svelte, mentor_agent)
├── docs/
│   ├── ARCHITECTURE.md           # Technical deep-dive
│   ├── MCP_SPEC.md               # MCP server design spec
│   ├── ROADMAP.md                # Roadmap and audit log
│   └── archive/                  # Historical specs
├── .mcp.json                     # Claude Code MCP server config
├── SOUL.md                       # Athena's personality (→ Claude project prompt)
├── README.md                     # Project overview + quick start
└── CLAUDE.md                     # This file
```

## Running

### MCP Server (local)

```bash
cd backend
pip3 install -r requirements.txt
python3 mcp_server.py              # starts on stdio (MCP transport)
```

The server starts automatically when Claude Desktop or Claude Code connects — you don't need to run it manually. Configure via `.mcp.json` (Claude Code) or `claude_desktop_config.json` (Claude Desktop).

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full technical deep-dive.

Key points for development:
- `vault/_meta/schema.md` is the single source of truth — parsed at boot, drives everything
- `mcp_server.py` boots the graph, registers 17 tools via FastMCP
- 7 domains, 27 types — never hardcode, always derive from schema
- Wikilinks under `## Section` headings define edge types (see ARCHITECTURE.md for full mapping)
- The MCP server makes zero external API calls — all processing is local

## MCP Tools

### Search & Read
- `search_vault(query, n, types, domains)` — semantic search with filters
- `read_node(node_id)` — full content + frontmatter + neighbors
- `list_nodes(type, domain, status)` — filtered listing
- `get_graph_stats()` — counts and breakdowns
- `get_schema()` — full schema definition
- `get_activity(limit)` — recent timeline

### Write
- `write_node(node_id, title, type, content, frontmatter, edges)` — create with dedup/permanence checks
- `update_node(node_id, ...)` — patch with cascade proposals
- `delete_node(node_id)` — archive to `_backup/`

### Graph
- `traverse_neighbors(node_id, depth)` — multi-hop traversal
- `find_cross_references(node_id)` — suggest links

### Analysis
- `detect_conflicts(message)` — check intention against graph
- `check_accountability()` — streaks, overdue, fundamentals
- `check_relationships(lookback_days)` — person health and drift
- `audit_vault()` — structural issues

### Admin
- `rebuild_vault()` — full re-parse + re-index
- `vault_repair()` — fix structural issues

## Code Style

- Python: snake_case, type hints, docstrings on public methods, `from __future__ import annotations`
- Keep modules focused — each backend file does one thing
- No over-engineering. Simple > clever.

## Things to Never Do

- Never commit `.env` or API keys
- Never add telemetry, analytics, or external tracking
- Never store user data outside `vault/`
- Never make outbound API calls from the MCP server
- Never hardcode type lists — derive from schema
