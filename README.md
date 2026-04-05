# Athena

A personal knowledge graph with an AI interface that captures, connects, and explores everything that matters to you.

Athena builds a graph about your life — goals, fears, people, places, habits, finances, ideas, plans — stored as plain markdown files. Claude connects to the graph via MCP and acts as Athena: sharp, direct, and strategic. She thinks in systems, surfaces connections you'd miss, and aggressively proposes graph updates from every conversation.

**Local-first.** The vault lives on your machine as plain markdown. The MCP server does graph traversal, vector search, and deterministic analysis — no API calls. Claude does all reasoning natively via your subscription.

## Quick Start

```bash
# Install dependencies
cd backend
pip3 install -r requirements.txt

# Set vault path
echo "VAULT_PATH=../vault" > .env
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "athena": {
      "command": "python3",
      "args": ["/path/to/athena/backend/mcp_server.py"],
      "env": {
        "VAULT_PATH": "/path/to/athena/vault"
      }
    }
  }
}
```

Then create a **Project** in Claude Desktop and paste [SOUL.md](SOUL.md) as the project instructions.

### Claude Code

The `.mcp.json` in the project root auto-configures the server.

## Tech Stack

| Layer | Tech |
|-------|------|
| Data store | Markdown files + YAML frontmatter in `vault/` |
| Schema | `vault/_meta/schema.md` — single source of truth |
| Graph engine | NetworkX — in-memory directed graph |
| Vector search | ChromaDB — local semantic similarity |
| MCP server | Python (FastMCP) — 17 tools, no API calls |
| AI reasoning | Claude via subscription (Desktop, Code, or claude.ai) |

## Project Structure

```
athena/
├── vault/                    # Knowledge graph (markdown, git-tracked)
│   ├── Self/People/Knowledge/Life/Planning/Places/Finance/
│   ├── _meta/schema.md       # Executable schema — domains, types, edges
│   └── _templates/            # Node file templates
├── backend/
│   ├── mcp_server.py          # MCP server entry point (FastMCP, 17 tools)
│   ├── permanence.py          # Node permanence levels and scoring
│   ├── vault_parser.py        # Markdown → nodes + edges
│   ├── vault_graph.py         # NetworkX graph wrapper
│   ├── schema_parser.py       # Parses schema.md at boot
│   ├── vector_search.py       # ChromaDB semantic search
│   ├── services/
│   │   ├── vault_service.py   # File I/O, cross-referencing, repair
│   │   ├── conflict_service.py # Contradiction detection
│   │   ├── accountability_service.py # Streaks, commitments, fundamentals
│   │   ├── relationship_service.py   # Person mention tracking, health
│   │   ├── audit_service.py   # Vault structural health
│   │   └── state_service.py   # User state inference
│   └── tests/                 # pytest suite
├── archive/                   # Archived V1 code (Flask frontend, mentor_agent)
├── docs/
│   ├── ARCHITECTURE.md        # Technical deep-dive
│   ├── MCP_SPEC.md            # MCP server design spec
│   └── ROADMAP.md             # Stage 1-4 roadmap
├── CLAUDE.md                  # Dev instructions (for Claude Code)
└── SOUL.md                    # Athena's personality (→ Claude project prompt)
```

## How It Works

### Schema-Driven

Everything flows from `vault/_meta/schema.md`. At boot, the MCP server parses it to extract domains, types, frontmatter fields, folder mappings, and edge types. Adding a new node type means editing schema.md — zero code changes.

### Two-Tier Type System

7 domains containing 27 types:

| Domain | Types |
|--------|-------|
| Self | goal, fear, belief, value, habit, skill |
| People | person, organisation |
| Knowledge | book, article, idea, note, interest, movie, quote, pill |
| Life | experience, daily, memory |
| Planning | task, project, reminder, event |
| Places | place |
| Finance | expense, subscription, budget |

### MCP Tools

Claude connects to the graph through 17 tools:

**Search & Read** — `search_vault`, `read_node`, `list_nodes`, `get_graph_stats`, `get_schema`, `get_activity`
**Write** — `write_node`, `update_node`, `delete_node`
**Graph** — `traverse_neighbors`, `find_cross_references`
**Analysis** — `detect_conflicts`, `check_accountability`, `check_relationships`, `audit_vault`
**Admin** — `rebuild_vault`, `vault_repair`

### Vault Format

Every node is a markdown file with YAML frontmatter. Relationships are `[[wikilinks]]` under section headings (`## Blockers` → `blocked_by`, `## People` → `involves`, etc.). Compatible with Obsidian.

See [ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full technical deep-dive.

## Athena's Personality

Athena is the goddess of wisdom and strategy. She sees the whole board — sharp, direct, no filler. Her personality is defined in [SOUL.md](SOUL.md) and loaded as a Claude project system prompt.

## Privacy

- All data stays on your machine in `vault/` as plain markdown
- The MCP server makes zero external calls — all processing is local
- Claude reasoning is handled by your subscription, same as any Claude conversation
- No telemetry, analytics, or tracking
