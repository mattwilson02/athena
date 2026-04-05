# Athena — Architecture

Technical reference for how the system works. For project overview and setup, see [README.md](../README.md). For MCP server design, see [MCP_SPEC.md](MCP_SPEC.md).

---

## System Architecture

```
┌──────────────────────────────────┐
│  Claude (Desktop / Code / Web)   │
│  - SOUL.md as project prompt     │
│  - Native reasoning, streaming   │
│  - Calls Athena tools as needed  │
└──────────┬───────────────────────┘
           │ MCP (stdio)
           ▼
┌──────────────────────────────────┐
│  Athena MCP Server (Python)      │
│  17 tools — zero API calls       │
│  ├─ NetworkX graph engine        │
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
│  7 domains, 27 types             │
│  git-tracked, Obsidian-compatible│
└──────────────────────────────────┘
```

Claude does all reasoning natively. The MCP server is a deterministic data layer — graph traversal, vector search, conflict detection, and file I/O.

---

## Boot Sequence

When `python3 mcp_server.py` runs:

```
1. parse_schema("vault/_meta/schema.md")
   → domains, types, frontmatter fields, folder mappings, edge types

2. VaultParser(vault_path, edge_map).parse()
   → walks vault/, reads every .md file, extracts frontmatter + wikilinks
   → returns (nodes[], edges[])

3. VaultGraph.build_from_parsed(nodes, edges)
   → populates NetworkX DiGraph, skips dangling edges

4. VectorIndex.rebuild(nodes)
   → indexes all node content into ChromaDB for semantic search

5. VaultService instantiated
   → receives graph, vector_index, schema, rebuild/refresh functions

6. FastMCP registers 17 tools
   → each tool closes over graph, vector_index, schema, vault_service

7. MCP server starts (stdio transport)
   → ready for Claude to call tools
```

---

## Tool Categories

### Search & Read
| Tool | Purpose |
|------|---------|
| `search_vault` | Semantic search with optional type/domain filters |
| `read_node` | Full node content + frontmatter + neighbors |
| `list_nodes` | Filter by type, domain, or status |
| `get_graph_stats` | Total nodes, edges, type/domain breakdowns |
| `get_schema` | Full schema — types, domains, edges, fields |
| `get_activity` | Recent create/update timeline |

### Write
| Tool | Purpose |
|------|---------|
| `write_node` | Create node with dedup check + permanence warning |
| `update_node` | Patch node with cascade proposals |
| `delete_node` | Archive to `_backup/` |

### Graph
| Tool | Purpose |
|------|---------|
| `traverse_neighbors` | Multi-hop traversal (1-3 hops) |
| `find_cross_references` | Suggest links based on content similarity |

### Analysis
| Tool | Purpose |
|------|---------|
| `detect_conflicts` | Check intention against graph for contradictions |
| `check_accountability` | Streaks, overdue commitments, fundamentals |
| `check_relationships` | Person health, mention frequency, drift |
| `audit_vault` | Stale statuses, orphans, broken links |

### Admin
| Tool | Purpose |
|------|---------|
| `rebuild_vault` | Full re-parse + re-index |
| `vault_repair` | Fix structural issues in vault files |

---

## Node Write Lifecycle

```
Claude calls write_node(node_id, title, type, content, frontmatter, edges)
        │
        ▼
MCP Server
  ├─ Validate type against schema.type_list
  ├─ Dedup check via vector search (threshold 0.85)
  ├─ Check permanence level → return warning if identity/fundamental
  │
  ▼
VaultService.write()
  ├─ _sanitize_id() — lowercase, hyphens, strip special chars
  ├─ get_folder_for_type() — resolve folder from schema
  ├─ Build markdown: YAML frontmatter + # Title + content + wikilinks
  ├─ Write to vault/{folder}/{node_id}.md
  ├─ refresh_after_write() — re-parse vault, upsert to vector index
  └─ find_cross_references(node_id)
        │
        ▼
Returns {ok, filepath, stats, suggested_links, duplicate_warnings, permanence_warning}
  → Claude sees warnings and acts accordingly
```

---

## Two-Tier Type System

7 domains, 27 types. The schema defines everything:

```
Self         → goal, fear, belief, value, habit, skill
People       → person, organisation
Knowledge    → book, article, idea, note, interest, movie, quote, pill
Life         → experience, daily, memory
Planning     → task, project, reminder, event
Places       → place
Finance      → expense, subscription, budget
```

Each type specifies:
- **Domain** — which domain it belongs to
- **Folder** — vault path (e.g. `Self/Goals`)
- **Frontmatter** — type-specific YAML fields (e.g. `status`, `priority` for goals)
- **Description** — used by Claude for type selection

---

## Vault File Format

Every node is a markdown file:

```markdown
---
id: learn-piano
type: goal
title: Learn Piano
created: 2026-01-15
updated: 2026-02-20
tags:
  - music
  - learning
status: active
priority: high
---

# Learn Piano

I want to learn to play piano at an intermediate level.

## Blockers
- [[time-management]]

## Related
- [[music-theory]]

## People
- [[alice]]
```

**Frontmatter**: YAML between `---` fences. Contains `id`, `type`, `title`, timestamps, tags, and type-specific fields.

**Relationships**: `[[wikilinks]]` under `## Section` headings. The section heading determines the edge type:

| Section Heading | Edge Type |
|-----------------|-----------|
| `## Blockers` | `blocked_by` |
| `## Supports` | `supported_by` |
| `## Related` | `relates_to` |
| `## Contradicts` | `contradicts` |
| `## Inspired By` | `inspired_by` |
| `## People` | `involves` |
| `## Part Of` | `part_of` |
| `## Located In` | `located_in` |
| `## Funded By` | `funded_by` |
| `## Met At` | `met_at` |
| Outside any section | `relates_to` (default) |

Compatible with Obsidian — wikilinks render as clickable references.

---

## Extending the Schema

Adding a new node type requires zero code changes:

1. **Edit `vault/_meta/schema.md`** — add a `### type_name` block under the TYPES section
2. **Add a domain row** if the type belongs to a new domain
3. **Create a template** in `vault/_templates/` (optional)
4. **Create the folder** in `vault/`
5. **Restart the MCP server** — schema is parsed at boot

Claude sees the updated schema via `get_schema` and adapts automatically.

---

## Backend Structure

```
mcp_server.py          Boot sequence + 17 tool definitions (FastMCP)
permanence.py          Permanence levels, status penalties, scoring
vault_parser.py        Markdown → nodes + edges
vault_graph.py         NetworkX graph wrapper
schema_parser.py       Parses schema.md at boot
vector_search.py       ChromaDB semantic search
services/
  vault_service.py     File I/O, cross-referencing, repair, cascade
  conflict_service.py  Contradiction detection (signals + topic matching)
  accountability_service.py  Streaks, overdue, fundamentals monitoring
  relationship_service.py    Person mentions, health scoring
  state_service.py     User state inference from message patterns
  audit_service.py     Vault structural health scanning
```
