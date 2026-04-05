# Athena MCP Server — Design Spec

> Refactor from Flask API + Svelte frontend + Claude API to an MCP server. Claude becomes the reasoning layer natively via subscription. Athena becomes a tool provider — graph engine, vault I/O, and deterministic analysis.

---

## Why

The current architecture has three layers: Svelte frontend → Flask API → Claude API. Two of those are redundant now that Claude supports MCP natively. The knowledge graph, conflict detection, accountability engine, and vault structure are the real product — the Flask routes and prompt engineering are just glue.

**What goes away:**
- Flask server, all routes, auth middleware
- `mentor_agent.py` — the 98KB orchestration layer that builds system prompts, manages retrieval, calls the Anthropic SDK
- `chat_service.py` — message pipeline that chains conflict detection → mentor → graph updates
- `claude_client.py` — Anthropic SDK wrapper
- Svelte frontend (archived, not deleted)
- `ANTHROPIC_API_KEY` — no more API billing
- Docker multi-container setup (proxy + frontend + backend)

**What stays:**
- Vault (markdown + YAML frontmatter, schema, templates)
- NetworkX graph engine
- ChromaDB vector search
- All deterministic services: conflict, accountability, relationship, audit, vault, state
- Schema parser, vault parser
- SOUL.md (becomes Claude project system prompt)

---

## Architecture

```
┌──────────────────────────────────┐
│  Claude (claude.ai / Code / CLI) │
│  - SOUL.md as project prompt     │
│  - Native reasoning, streaming   │
│  - Voice, artifacts, web search  │
└──────────┬───────────────────────┘
           │ MCP (stdio or SSE)
           ▼
┌──────────────────────────────────┐
│  Athena MCP Server (Python)      │
│                                  │
│  Tools:                          │
│  ├─ search_vault                 │
│  ├─ read_node                    │
│  ├─ list_nodes                   │
│  ├─ get_graph_stats              │
│  ├─ get_schema                   │
│  ├─ write_node                   │
│  ├─ update_node                  │
│  ├─ delete_node                  │
│  ├─ traverse_neighbors           │
│  ├─ find_cross_references        │
│  ├─ detect_conflicts             │
│  ├─ check_accountability         │
│  ├─ check_relationships          │
│  ├─ audit_vault                  │
│  ├─ rebuild_vault                │
│  ├─ vault_repair                 │
│  └─ get_activity                 │
│                                  │
│  Resources:                      │
│  ├─ athena://schema              │
│  └─ athena://soul                │
│                                  │
│  Internals (no API calls):       │
│  ├─ NetworkX graph               │
│  ├─ ChromaDB vector index        │
│  ├─ Schema parser                │
│  ├─ Vault parser + writer        │
│  ├─ Conflict service             │
│  ├─ Accountability service       │
│  ├─ Relationship service         │
│  └─ Audit service                │
└──────────┬───────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│  vault/ (markdown + YAML)        │
│  Same structure, same schema     │
└──────────────────────────────────┘
```

---

## Boot Sequence

```
1. parse_schema("vault/_meta/schema.md")
   → domains, types, frontmatter fields, folder mappings, edge types

2. VaultParser(vault_path, edge_map).parse()
   → walks vault/, reads every .md, extracts frontmatter + wikilinks
   → returns (nodes[], edges[])

3. VaultGraph.build_from_parsed(nodes, edges)
   → populates NetworkX DiGraph, skips dangling edges

4. VectorIndex.rebuild(nodes)
   → indexes all node content into ChromaDB for semantic search

5. Instantiate services (vault, conflict, accountability, relationship, audit)
   → all receive references to graph, vector_index, schema

6. Register MCP tools and resources
   → each tool handler closes over the services it needs

7. Start MCP server (stdio for Claude Code, SSE for claude.ai)
```

---

## MCP Tools

### Read Tools

#### `search_vault`
Semantic search over the knowledge graph.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `query` | str | required | Search query |
| `n` | int | 10 | Max results |
| `types` | list[str] | null | Filter by node type |
| `domains` | list[str] | null | Filter by domain |

Returns: ranked list of `{id, title, type, domain, status, score, snippet}`.

#### `read_node`
Read a single node with full content and neighbors.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `node_id` | str | required | Node ID (filename without .md) |

Returns: `{id, title, type, domain, status, content, frontmatter, neighbors: [{id, title, type, edge_type}]}`.

#### `list_nodes`
List nodes filtered by type or domain.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | str | null | Filter by type |
| `domain` | str | null | Filter by domain |
| `status` | str | null | Filter by status |

Returns: list of `{id, title, type, domain, status, created, updated}`.

#### `get_graph_stats`
Graph overview statistics.

Returns: `{total_nodes, total_edges, nodes_by_type, nodes_by_domain, nodes_by_status}`.

#### `get_schema`
The full vault schema — domains, types, edges, frontmatter fields, colours.

Returns: parsed schema dict.

#### `get_activity`
Recent vault activity timeline.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 30 | Max entries |

Returns: chronological list of `{id, title, type, action, date}`.

### Write Tools

#### `write_node`
Create a new node in the vault.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `node_id` | str | required | Node ID (becomes filename) |
| `title` | str | required | Node title |
| `type` | str | required | Must be valid schema type |
| `content` | str | "" | Markdown body |
| `frontmatter` | dict | {} | YAML frontmatter fields |
| `edges` | list[str] | [] | Wikilink targets |

Built-in validation:
- Rejects invalid types (not in schema)
- Dedup check via vector search — returns `duplicate_warnings` if similar nodes exist
- Returns `permanence_warning` if creating identity/fundamental-level node
- Returns `suggested_links` from cross-reference scan

Returns: `{ok, filepath, stats, suggested_links, duplicate_warnings, permanence_warning}`.

#### `update_node`
Patch an existing node.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `node_id` | str | required | Node ID |
| `title` | str | null | New title |
| `content` | str | null | Replace content |
| `append_content` | str | null | Append to content |
| `frontmatter` | dict | null | Merge into frontmatter |
| `add_tags` | list[str] | null | Tags to add |
| `remove_tags` | list[str] | null | Tags to remove |
| `add_edges` | list[str] | null | Wikilinks to add |
| `status` | str | null | New status |

Built-in validation:
- Supersession check: if status → `superseded`, requires `superseded_by` in frontmatter
- Returns `cascade_proposals` for connected nodes affected by the change
- Returns `permanence_warning` if modifying identity/fundamental node

Returns: `{ok, stats, cascade_proposals, permanence_warning}`.

#### `delete_node`
Archive a node to `_backup/`.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `node_id` | str | required | Node ID |

Returns: `{ok, archived_to}`.

### Graph Tools

#### `traverse_neighbors`
Multi-hop graph traversal from a node.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `node_id` | str | required | Starting node |
| `depth` | int | 1 | Hop depth (max 3) |

Returns: neighbors grouped by hop distance, with edge types.

#### `find_cross_references`
Suggest links for a node based on content similarity and graph structure.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `node_id` | str | required | Node ID |

Returns: list of `{target_id, target_title, reason, score}`.

### Analysis Tools

#### `detect_conflicts`
Check a user intention against existing graph nodes for contradictions.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `message` | str | required | The user's message or stated intention |

Returns: list of `{node_id, title, conflict_type, severity, explanation}`.

Conflict types: `goal_contradiction`, `value_violation`, `schedule_conflict`, `habit_break`, `priority_inversion`.

#### `check_accountability`
Get habit streaks, overdue commitments, and fundamentals status.

Returns: `{streaks: [...], overdue: [...], fundamentals: [...], patterns: [...]}`.

#### `check_relationships`
Relationship health across all tracked people.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `lookback_days` | int | 30 | Analysis window |

Returns: per-person `{name, health, mention_count, days_since_last, drift_alert}`.

#### `audit_vault`
Structural health scan of the vault.

Returns: `{stale_statuses, orphan_nodes, broken_wikilinks, type_mismatches}`.

### Admin Tools

#### `rebuild_vault`
Force full re-parse of vault and re-index of vectors. Use after manual vault edits.

Returns: `{nodes, edges, indexed}`.

#### `vault_repair`
Walk vault and fix structural issues (duplicate sections, heading problems).

Returns: `{repaired: [...]}`.

---

## MCP Resources

#### `athena://schema`
The parsed vault schema. Claude should read this to understand available types, domains, edges, and frontmatter fields before writing nodes.

#### `athena://soul`
SOUL.md content. Provides Athena's identity, voice, conflict protocol, and behavioral boundaries. Also set as Claude project system prompt — this resource is a fallback for contexts where project prompts aren't available (e.g. Claude Code).

---

## Extraction: `permanence.py`

Three services import `_get_permanence` from `mentor_agent.py`. Before archiving mentor_agent, extract into `backend/permanence.py`:

```python
PERMANENCE_DEFAULTS = {
    "value": "fundamental",
    "belief": "identity",
    "fear": "identity",
    "goal": "strategic",
    "habit": "strategic",
    "skill": "identity",
    # ... all type defaults
}

STATUS_PENALTIES = {
    "completed": -0.15,
    "abandoned": -0.20,
    "paused": -0.10,
    "superseded": -0.20,
}

def get_permanence(node: dict) -> str:
    """Return permanence level from frontmatter or type-based default."""
    return node.get("permanence") or PERMANENCE_DEFAULTS.get(node.get("type"), "tactical")
```

Update imports in: `vault_service.py`, `conflict_service.py`, `accountability_service.py`.

---

## What Claude Handles Natively (no tool needed)

These capabilities from mentor_agent.py are replaced by Claude's native reasoning:

| Old capability | Claude replacement |
|---|---|
| System prompt construction | SOUL.md as project prompt |
| Mode classification (Mirror/Advisor/Guardian/Dialectic) | Claude reads SOUL.md and adapts tone naturally |
| Query intent classification | Claude decides which tools to call |
| Token budget management | Claude manages its own context window |
| Response formatting | Claude's native streaming |
| Bootstrap conversation | Claude sees empty stats via `get_graph_stats` and adapts |
| Retrieval pipeline (scoring, ranking, tiers) | Claude calls `search_vault` and `read_node` as needed |
| Domain keyword heuristics | Claude's semantic understanding |
| Insights generation | Claude reasons over `audit_vault` + `check_accountability` data |

---

## SOUL.md Additions

SOUL.md becomes the Claude project system prompt. Add a section guiding tool usage:

```markdown
## Tool Usage Protocol

- **Every conversation**: call `check_accountability` to surface overdue commitments and broken streaks. Mention anything urgent before responding to the user's question.
- **When the user expresses an intention** ("I'm going to", "I want to", "planning to"): call `detect_conflicts` with their message. Follow the Conflict Protocol for any results.
- **When creating or modifying nodes**: always call `write_node` or `update_node` — never tell the user you've updated the graph without actually calling the tool.
- **When the user asks about a person**: call `check_relationships` to get health data. Call `read_node` on the person node and `traverse_neighbors` to see connected experiences.
- **When the user asks broad questions** ("what happened this week", "how am I doing"): call `search_vault` with appropriate filters, then `get_activity` for timeline context.
- **Periodically**: call `audit_vault` and surface any structural issues worth fixing.
```

---

## Directory Structure (after refactor)

```
athena/
├── vault/                          # Unchanged
├── backend/
│   ├── mcp_server.py               # MCP server entry point
│   ├── tools/                      # MCP tool handlers
│   │   ├── __init__.py
│   │   ├── search_tools.py         # search_vault, list_nodes, get_activity
│   │   ├── read_tools.py           # read_node, get_graph_stats, get_schema
│   │   ├── write_tools.py          # write_node, update_node, delete_node
│   │   ├── graph_tools.py          # traverse_neighbors, find_cross_references
│   │   ├── analysis_tools.py       # detect_conflicts, check_accountability, check_relationships, audit_vault
│   │   └── admin_tools.py          # rebuild_vault, vault_repair
│   ├── permanence.py               # Extracted from mentor_agent.py
│   ├── vault_parser.py             # Unchanged
│   ├── vault_graph.py              # Unchanged
│   ├── vector_search.py            # Unchanged
│   ├── schema_parser.py            # Unchanged
│   ├── services/
│   │   ├── vault_service.py        # Import path fix only
│   │   ├── conflict_service.py     # Import path fix only
│   │   ├── accountability_service.py # Import path fix only
│   │   ├── relationship_service.py # Simplified: node frontmatter only
│   │   ├── state_service.py        # May drop — Claude assesses state natively
│   │   └── audit_service.py        # Unchanged
│   ├── middleware/
│   │   └── security.py             # Path traversal prevention (kept)
│   ├── tests/                      # Updated for MCP tools
│   └── requirements.txt            # Flask/anthropic removed, mcp added
├── archive/                        # Archived code
│   ├── frontend/                   # Full Svelte frontend
│   ├── mentor_agent.py
│   ├── claude_client.py
│   ├── chat_store.py
│   ├── server.py
│   ├── routes/
│   ├── services/chat_service.py
│   └── middleware/
├── SOUL.md                         # Unchanged + tool protocol section
├── CLAUDE.md                       # Updated for MCP architecture
├── docs/
│   ├── ARCHITECTURE.md             # Updated
│   ├── MCP_SPEC.md                 # This file
│   └── archive/                    # Old specs
└── .mcp.json                       # Claude Code server config
```

---

## Configuration

### Claude Code (`.mcp.json` in project root)
```json
{
  "mcpServers": {
    "athena": {
      "command": "python3",
      "args": ["backend/mcp_server.py"],
      "env": {
        "VAULT_PATH": "./vault"
      }
    }
  }
}
```

### claude.ai
Deploy as SSE endpoint (e.g. via Render, Fly.io, or local tunnel) and add as remote MCP server in Claude settings.

---

## Implementation Phases

### Phase 1: Extract and decouple
1. Create `backend/permanence.py` — extract constants + `get_permanence()`
2. Update imports in vault_service, conflict_service, accountability_service
3. Run existing tests — nothing should break

### Phase 2: MCP server skeleton + read tools
4. Create `backend/mcp_server.py` with boot sequence
5. Create `backend/tools/` — read_tools, search_tools
6. Implement: `search_vault`, `read_node`, `list_nodes`, `get_graph_stats`, `get_schema`, `get_activity`
7. Wire into Claude Code via `.mcp.json`, test manually

### Phase 3: Write tools
8. Implement: `write_node` (with dedup, permanence warnings, suggested links)
9. Implement: `update_node` (with cascade, supersession validation)
10. Implement: `delete_node`
11. Test vault writes through Claude

### Phase 4: Analysis + graph tools
12. Implement: `detect_conflicts`, `check_accountability`, `check_relationships`
13. Implement: `traverse_neighbors`, `find_cross_references`, `audit_vault`
14. Test full conversation flow — Claude reading graph, detecting conflicts, writing nodes

### Phase 5: Archive and document
15. Move archived files to `archive/`
16. Update `requirements.txt` — remove Flask, anthropic; add mcp
17. Add tool protocol section to SOUL.md
18. Update CLAUDE.md, ARCHITECTURE.md
19. Update/add tests for MCP tool handlers

---

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Claude doesn't call `detect_conflicts` proactively | Conflicts silently missed | Tool protocol in SOUL.md + periodic reminder in project prompt |
| Retrieval quality drops without scoring pipeline | Less relevant context | Rich metadata in `search_vault` responses so Claude can reason about relevance |
| Challenge ladder lost | Identity changes too easy | `permanence_warning` in write/update tool responses signals Claude to challenge |
| No graph visualisation | Can't see structure | Separate lightweight viewer later, or Claude describes structure in text |
| MCP transport latency | Slow tool calls | Boot once, keep server running; all operations are local I/O |
| SOUL.md instructions ignored by Claude | Personality drift | Test thoroughly; project prompts are reliable in practice |

---

## Open Questions

1. **State service** — keep or drop? Claude can assess user state from conversation natively. The heuristic `state_service.py` may be redundant. Leaning drop.
2. **Chat history** — relationship_service currently scans chat sessions for mention frequency. In MCP world, simplify to work from person node frontmatter (`last_mentioned`, `mention_count_30d`). If mention tracking is important, add a lightweight `log_mention` tool.
3. **SSE transport** — for claude.ai access, the MCP server needs an HTTP endpoint. Deploy separately or tunnel? Defer to Phase 5+.
4. **Graph visualisation** — the force-directed graph view is genuinely useful for understanding structure. Build a minimal standalone viewer later? Or accept text-based exploration via tools.

---

*Spec written: April 2026 — Stage 2 prep*
