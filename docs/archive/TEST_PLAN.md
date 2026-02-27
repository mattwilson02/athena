# Athena — Test Coverage Plan

> Current test coverage: **zero**. No test files, no test runner, no CI.

This plan prioritises tests by blast radius — things that silently corrupt data or break the graph get tested first. AI response quality is tested manually via conversation.

---

## 1. Setup

### Backend (pytest)

```bash
# Add to requirements.txt
pytest
pytest-cov

# Directory structure
backend/
  tests/
    __init__.py
    conftest.py               # Shared fixtures (tmp vault, test schema, mock graph)
    test_vault_parser.py
    test_vault_graph.py
    test_schema_parser.py
    test_vector_search.py
    test_chat_store.py
    test_vault_service.py
    test_chat_service.py
    test_routes.py
```

### `conftest.py` — Shared Fixtures

```python
@pytest.fixture
def tmp_vault(tmp_path):
    """Create a minimal vault with schema, 3-4 test nodes, and wikilinks."""

@pytest.fixture
def schema():
    """Parse the real schema.md and return the schema dict."""

@pytest.fixture
def graph(tmp_vault, schema):
    """Parse tmp_vault and build a VaultGraph."""

@pytest.fixture
def app(tmp_vault, schema, graph):
    """Flask test client with all services wired up."""
```

---

## 2. Priority 1 — Data Integrity (test first, break = data loss)

### `test_vault_parser.py` — Markdown → Nodes + Edges

These tests protect against silent data corruption when reading vault files.

| Test | What it verifies |
|------|------------------|
| `test_parse_node_with_full_frontmatter` | All YAML fields extracted correctly (id, type, title, created, updated, tags, custom fields) |
| `test_parse_node_minimal_frontmatter` | Node with only required fields (id, type, title) still parses |
| `test_parse_wikilinks_under_sections` | `[[target]]` under `## Blockers` → edge type `blocked_by` |
| `test_parse_wikilinks_default_section` | `[[target]]` outside any section → edge type `relates_to` |
| `test_parse_multiple_edge_sections` | Node with `## Related`, `## People`, `## Part Of` → 3 different edge types |
| `test_parse_ignores_backup_dir` | Files in `_backup/` are not parsed |
| `test_parse_ignores_meta_dir` | Files in `_meta/` are not parsed |
| `test_parse_ignores_templates_dir` | Files in `_templates/` are not parsed |
| `test_parse_content_extraction` | Body text (below frontmatter, excluding sections with wikilinks) captured correctly |
| `test_parse_handles_empty_file` | Empty .md file doesn't crash parser |
| `test_parse_handles_malformed_frontmatter` | Missing `---` delimiters → file skipped gracefully |
| `test_parse_strips_leading_newlines` | Leading `\n` between frontmatter and body stripped (V3 fix) |

### `test_vault_service.py` — Write + Update (vault file mutations)

These tests protect against corrupting vault files when writing or updating nodes.

| Test | What it verifies |
|------|------------------|
| `test_write_creates_file_in_correct_folder` | `type: goal` → writes to `vault/Self/Goals/my-goal.md` |
| `test_write_generates_valid_frontmatter` | Written file has correct YAML frontmatter with all required fields |
| `test_write_sanitises_id` | Underscores → hyphens, lowercase, no special chars |
| `test_write_rejects_invalid_type` | Type not in schema → raises error |
| `test_write_adds_wikilinks_under_sections` | Edges written as `[[target]]` under correct `## Section` heading |
| `test_write_dedup_sections` | Duplicate `## Related` headings merged into one (V3 fix) |
| `test_update_title` | `changes={title: "New Title"}` → frontmatter title updated, body unchanged |
| `test_update_content` | `changes={content: "New body"}` → body replaced, frontmatter unchanged |
| `test_update_add_tags` | `changes={add_tags: ["new"]}` → tag appended to frontmatter list |
| `test_update_remove_tags` | `changes={remove_tags: ["old"]}` → tag removed from frontmatter list |
| `test_update_add_edge` | `changes={add_edges: [{target, type}]}` → wikilink added under correct section |
| `test_update_remove_edge` | `changes={remove_edges: [{target}]}` → wikilink removed |
| `test_update_frontmatter_field` | `changes={frontmatter: {status: "done"}}` → field updated in YAML |
| `test_write_roundtrip` | Write node → parse it back → all fields match |

### `test_vault_graph.py` — Graph Operations

| Test | What it verifies |
|------|------------------|
| `test_build_from_parsed_nodes_and_edges` | All nodes and valid edges added to graph |
| `test_dangling_edge_skipped` | Edge to nonexistent node → warning, not crash |
| `test_get_node_returns_all_fields` | Node dict includes id, type, title, content, tags, custom fields |
| `test_get_neighbors_returns_connected_nodes` | Correct neighbor set for a connected node |
| `test_get_neighbors_with_edges` | Returns edge type + direction for each neighbor |
| `test_get_stats` | Correct total_nodes, total_edges, per-type counts |
| `test_get_nodes_by_type` | Filters correctly by type |
| `test_get_neighbors_by_hop` | 2-hop traversal returns correct nodes at each tier |

---

## 3. Priority 2 — Core Features (test second, break = broken UX)

### `test_schema_parser.py` — Schema Parsing

| Test | What it verifies |
|------|------------------|
| `test_parse_domains` | All 7 domains extracted with correct names |
| `test_parse_types` | All 27 types extracted and mapped to correct domains |
| `test_parse_edge_types` | All 10 edge types extracted with section heading mappings |
| `test_parse_frontmatter_fields` | Per-type frontmatter fields extracted |
| `test_parse_folder_mappings` | `goal` → `Self/Goals`, `person` → `People/Persons`, etc. |
| `test_type_list` | `schema.type_list` contains all type names as a flat list |

### `test_chat_store.py` — Session Persistence

| Test | What it verifies |
|------|------------------|
| `test_create_session` | New session gets UUID, title, timestamps |
| `test_list_sessions_sorted` | Sessions returned newest-first |
| `test_get_session_with_messages` | Messages round-trip through save/load |
| `test_append_message_with_graph_updates` | graph_updates stored alongside message |
| `test_delete_session` | File removed, no longer in list |
| `test_rename_session` | Title updated, other fields unchanged |
| `test_dismiss_update` | Update key added to dismissed set |
| `test_get_session_node_ids` | Returns set of all node IDs from graph_updates in session |

### `test_vector_search.py` — Semantic Search

| Test | What it verifies |
|------|------------------|
| `test_index_and_search` | Index 5 nodes → search "goals" → returns relevant results |
| `test_search_returns_scores` | Results include distance scores |
| `test_find_duplicates` | Two near-identical nodes detected as duplicates |
| `test_rebuild_reindexes` | After rebuild, search still works |

---

## 4. Priority 3 — API Routes (test third, break = frontend errors)

### `test_routes.py` — Flask Integration Tests

Use Flask test client. No actual Claude API calls — mock `mentor_agent.chat()`.

| Test | What it verifies |
|------|------------------|
| **Chat routes** | |
| `test_get_sessions` | `GET /api/chat/sessions` → 200, returns session list |
| `test_create_session` | `POST /api/chat/sessions` → 201, returns new session |
| `test_get_session` | `GET /api/chat/sessions/:id` → 200, includes messages |
| `test_delete_session` | `DELETE /api/chat/sessions/:id` → 200, session gone |
| `test_rename_session` | `PATCH /api/chat/sessions/:id` → 200, title updated |
| `test_send_message` | `POST /api/chat` with mock mentor → 200, response + graph_updates |
| `test_stream_message` | `POST /api/chat/stream` → SSE events (text + done) |
| **Graph routes** | |
| `test_get_graph` | `GET /api/graph` → 200, nodes + edges arrays |
| `test_get_stats` | `GET /api/graph/stats` → 200, type counts |
| `test_get_node` | `GET /api/node/:id` → 200, node + neighbors |
| `test_get_node_not_found` | `GET /api/node/nonexistent` → 404 |
| `test_search_title_match` | `GET /api/search?q=exact-title` → result with match_type "title" |
| `test_search_semantic` | `GET /api/search?q=vague query` → results with match_type "semantic" |
| `test_get_activity` | `GET /api/activity` → 200, sorted activity list |
| `test_get_schema` | `GET /api/schema` → 200, domains + types + edge_types |
| **Vault routes** | |
| `test_vault_write` | `POST /api/vault/write` → 200, file created on disk |
| `test_vault_write_invalid_type` | `POST /api/vault/write` with bad type → 400 |
| `test_vault_update` | `POST /api/vault/update` → 200, file modified |
| `test_vault_rebuild` | `POST /api/vault/rebuild` → 200, graph rebuilt |

---

## 5. What NOT to Test

| Category | Why |
|----------|-----|
| Claude API response quality | Non-deterministic. Test via real conversations. |
| `mentor_agent.chat()` end-to-end | Requires API key, slow, non-deterministic. Mock for route tests. |
| `mentor_agent.get_context()` ranking | Heavily dependent on actual vault content. Test the scoring logic in isolation if it gets complex. |
| GraphView.svelte rendering | Canvas-based, hard to unit test. Manual testing. |
| CSS / visual styling | Manual testing. |
| Insights endpoint quality | Depends on Claude. Test that the endpoint doesn't crash, not that the insight is good. |

---

## 6. Implementation Order

1. **Setup**: Add pytest + conftest.py with fixtures
2. **`test_vault_parser.py`** — highest blast radius, no external deps
3. **`test_vault_service.py`** — write/update roundtrips
4. **`test_vault_graph.py`** — graph operations
5. **`test_schema_parser.py`** — schema parsing
6. **`test_chat_store.py`** — session persistence
7. **`test_routes.py`** — API integration (needs mock mentor)
8. **`test_vector_search.py`** — ChromaDB (slowest, needs temp collection)

---

## 7. Running Tests

```bash
cd backend
python3 -m pytest tests/ -v
python3 -m pytest tests/ -v --cov=. --cov-report=term-missing
```

Target: **80%+ line coverage** on vault_parser, vault_service, vault_graph, schema_parser, chat_store. Route tests cover the happy path + key error cases.
