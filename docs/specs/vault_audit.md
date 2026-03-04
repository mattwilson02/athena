# Spec: Vault Audit Endpoint

**Epoch:** E1 Ground Truth — Item 1
**Files:** new `backend/services/audit_service.py`, `backend/routes/vault_routes.py`

---

## What

`POST /api/vault/audit` — scans every node in the graph for known issues, returns a structured report.

---

## Discovery Findings

### Available APIs

**VaultGraph** (`vault_graph.py`):
- `get_all_nodes()` → `list[dict]` — every node with all frontmatter fields + `id`, `content`, `filepath`
- `get_all_edges()` → `list[dict]` — every edge as `{source, target, type}`
- `get_degree(node_id)` → `int` — total in + out edges
- `get_node(node_id)` → `dict | None`

No `get_all_node_ids()` or `get_edges_from()` — the epoch spec's pseudocode was wrong. Use `get_all_nodes()` for node scanning and `get_all_edges()` for link validation.

**VaultParser** (`vault_parser.py`):
- `_extract_edges()` returns ALL wikilinks from the raw markdown, including targets that don't exist as nodes
- BUT: `VaultGraph.build_from_parsed()` silently drops edges where the target doesn't exist in the graph (line 33: "Dangling edge target, skipping")
- This means **broken wikilinks are invisible at the graph level**. They exist in the raw markdown but not in the NetworkX graph.

**Detection approach for broken wikilinks:** Re-parse raw markdown for each node, extract wikilink targets, check against the set of all node IDs. Cannot use the graph's edge data because dangling edges were already dropped.

**Schema** (`schema_parser.py`):
- `get_folder_for_type(schema, type_name)` → `str | None` — returns expected folder like `"Self/Goals"`
- Each node has `filepath` (relative to vault root, e.g., `"Self/Goals/learn-piano.md"`)
- Type mismatch = node's `filepath` doesn't start with the expected folder for its `type`

**Existing related endpoint:**
- `POST /api/vault/repair` — fixes duplicate sections and heading issues. Complementary, not overlapping. Audit reports problems; repair fixes a specific class of formatting issues.

### Access pattern

The audit service needs: `graph` (for nodes, edges, degrees), `schema` (for folder mapping), `vault_path` (for re-reading raw markdown to detect broken wikilinks). All available on `app.config`.

---

## Checks

### 1. Stale statuses
Nodes where:
- `status` is `active` or `planned`
- AND has a `date`, `due`, or `deadline` field with a value in the past

**Edge cases:**
- No `status` field → skip (not stale, just untracked)
- No date fields → skip (can't determine staleness)
- Future date + `completed` status → flag as anomaly (completed before due?)
- Nodes in `_templates/`, `_backup/`, `_meta/` → already excluded by parser, won't be in graph

### 2. Orphan nodes
Nodes where `get_degree(node_id) == 0`.

**Edge cases:**
- Schema node itself, if it somehow appears → skip
- Daily log entries may naturally have zero edges — still flag, user decides

### 3. Broken wikilinks
For each node, re-read the raw markdown file, extract all `[[wikilink]]` targets, check if each target exists in the set of all node IDs.

**Why not use graph edges:** `build_from_parsed()` drops dangling edges silently. They never enter the graph. Must go back to raw files.

**Edge cases:**
- Wikilinks in `_backup/` or `_templates/` → not scanned (parser skips these dirs)
- Wikilink target is an alias or has different casing → flag it (IDs are lowercase-hyphenated)

### 4. Type mismatches
For each node, check if `filepath` starts with the expected folder from `get_folder_for_type(schema, node_type)`.

**Edge cases:**
- Type not in schema → flag (type itself is invalid)
- Folder not in schema for this type → skip check (custom folder)

---

## API

```
POST /api/vault/audit

Response 200:
{
  "total_nodes": 105,
  "issues": {
    "stale_status": [
      {"id": "ethan-meetup-saturday", "status": "active", "date_field": "date", "date_value": "2026-02-25"}
    ],
    "orphans": [
      {"id": "orphan-node", "type": "note", "title": "Some orphaned note"}
    ],
    "broken_wikilinks": [
      {"source": "athena-dev-session", "target": "nonexistent-node", "file": "Planning/Tasks/athena-dev-session.md"}
    ],
    "type_mismatches": [
      {"id": "misplaced-node", "type": "goal", "filepath": "Knowledge/Notes/misplaced-node.md", "expected_folder": "Self/Goals"}
    ]
  },
  "summary": {
    "stale_status": 12,
    "orphans": 3,
    "broken_wikilinks": 5,
    "type_mismatches": 1
  }
}
```

Each issue includes enough context to understand and fix it without a second lookup.

---

## Implementation

New file: `backend/services/audit_service.py`

```python
def audit_vault(graph, schema, vault_path) -> dict:
```

Takes graph, schema, vault_path. Returns the report dict above. Pure function — no side effects, no writes.

**Broken wikilink detection:** Walk vault files (same pattern as `VaultParser.parse()` and `VaultService.repair()`), extract wikilinks with regex, check against `all_ids = {n["id"] for n in graph.get_all_nodes()}`.

Route in `vault_routes.py`:
```python
@vault_bp.route("/api/vault/audit", methods=["POST"])
def vault_audit():
    result = audit_vault(
        current_app.config["graph"],
        current_app.config["schema"],
        current_app.config["vault_path"],
    )
    return jsonify(result)
```

No new dependencies on `app.config` beyond what's already stored.

---

## Tests

In `backend/tests/test_audit_service.py`:

1. **Stale status detected** — node with past date + active status → appears in report
2. **Future date not flagged** — node with future date + active status → not flagged
3. **No status field skipped** — node without status → not flagged
4. **Orphan detected** — node with zero edges → appears in report
5. **Connected node not orphan** — node with edges → not in orphans
6. **Broken wikilink detected** — node with `[[nonexistent]]` in markdown → appears in report
7. **Valid wikilink not flagged** — node with `[[existing-node]]` → not in broken list
8. **Type mismatch detected** — node in wrong folder → appears in report
9. **Correct folder not flagged** — node in right folder → not flagged
10. **Empty vault returns zero issues**

---

## Verification

- `POST /api/vault/audit` returns valid JSON with all four issue categories
- Run against real vault — results make sense (sanity check before triage)
- All tests pass
