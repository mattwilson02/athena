# Vault Re-import

> E2 Item 6 — Process archived nodes from `_backup/` through the current schema, validate, dedup, and present as proposals for user review.

## Problem

There are 18 archived V1 nodes in `vault/_backup/v1/`. These were created before the schema was tightened (27 types, strict frontmatter, wikilink edges). Some have valid types, some might need remapping. Some might duplicate nodes that already exist in the live vault. There's no way to process them back into the vault without manually checking each one.

## Solution

A `POST /api/vault/import` endpoint that:
1. Reads all `.md` files from `_backup/` (recursively)
2. Parses each file's frontmatter + content
3. Validates type against current schema, suggests corrections for invalid types
4. Dedup-checks against existing vault nodes (exact ID match + semantic similarity)
5. Returns a batch of proposals — the user reviews and accepts/dismisses each one

No auto-import. Every node requires explicit user approval.

## API

### `POST /api/vault/import`

**Request body:** `{ "source": "_backup/v1" }` (optional, defaults to `_backup/`)

**Response:**
```json
{
  "proposals": [
    {
      "source_file": "_backup/v1/ethan-best-friend.md",
      "node_id": "ethan-best-friend",
      "title": "Ethan Shorthouse",
      "type": "person",
      "type_valid": true,
      "suggested_type": null,
      "content": "...",
      "frontmatter": { ... },
      "duplicate": null,
      "status": "ready"
    },
    {
      "source_file": "_backup/v1/ben-conway-mentor.md",
      "node_id": "ben-conway-mentor",
      "title": "Mentorship relationship with Ben Conway",
      "type": "experience",
      "type_valid": true,
      "suggested_type": null,
      "content": "...",
      "frontmatter": { ... },
      "duplicate": {
        "match": "semantic",
        "existing_id": "ben-conway",
        "existing_title": "Ben Conway",
        "score": 0.85
      },
      "status": "duplicate"
    },
    {
      "source_file": "_backup/v1/some-old-note.md",
      "node_id": "some-old-note",
      "title": "Some Old Note",
      "type": "lesson",
      "type_valid": false,
      "suggested_type": "pill",
      "content": "...",
      "frontmatter": { ... },
      "duplicate": null,
      "status": "needs_fix"
    }
  ],
  "summary": {
    "total": 18,
    "ready": 12,
    "duplicate": 4,
    "needs_fix": 2
  }
}
```

### `POST /api/vault/import/accept`

**Request body:**
```json
{
  "node_id": "ethan-best-friend",
  "type_override": null
}
```

Accepts a single proposal. If `type_override` is provided, uses that instead of the original type. Calls `vault_service.write()` under the hood — same validation, same folder resolution, same graph rebuild.

**Response:** Same as `POST /api/vault/write`.

## Implementation

### Files changed

| File | Change |
|------|--------|
| `backend/services/vault_service.py` | Add `import_proposals()` and `import_accept()` methods |
| `backend/routes/vault_routes.py` | Add `/api/vault/import` and `/api/vault/import/accept` endpoints |
| `backend/tests/test_vault_service.py` | Tests for import proposals |

### `vault_service.import_proposals(source_dir)`

```python
def import_proposals(self, source_dir: str = "_backup") -> dict:
    """Scan archived nodes, validate, dedup, return proposals."""
    backup_path = safe_resolve(Path(self.vault_path), source_dir)
    valid_types = set(self.schema.get("type_list", []))

    proposals = []
    for md_file in sorted(backup_path.rglob("*.md")):
        # Parse frontmatter + content (reuse vault_parser logic)
        fm, content = _parse_markdown(md_file)
        node_id = fm.get("id", md_file.stem)
        node_type = fm.get("type", "note")
        title = fm.get("title", node_id)

        # Type validation
        type_valid = node_type in valid_types
        suggested_type = _suggest_type(node_type, valid_types) if not type_valid else None

        # Dedup check
        duplicate = None
        existing = self.graph.get_node(node_id)
        if existing:
            duplicate = {
                "match": "exact_id",
                "existing_id": existing["id"],
                "existing_title": existing.get("title", existing["id"]),
            }
        else:
            matches = self.vector_index.find_duplicates(node_id, title, node_type)
            if matches:
                duplicate = {
                    "match": matches[0]["match"],
                    "existing_id": matches[0]["id"],
                    "existing_title": matches[0]["title"],
                    "score": matches[0].get("score", 0),
                }

        # Determine status
        if duplicate and duplicate["match"] == "exact_id":
            status = "duplicate"
        elif duplicate:
            status = "duplicate"
        elif not type_valid:
            status = "needs_fix"
        else:
            status = "ready"

        proposals.append({
            "source_file": str(md_file.relative_to(self.vault_path)),
            "node_id": node_id,
            "title": title,
            "type": node_type,
            "type_valid": type_valid,
            "suggested_type": suggested_type,
            "content": content,
            "frontmatter": fm,
            "duplicate": duplicate,
            "status": status,
        })

    summary = {
        "total": len(proposals),
        "ready": sum(1 for p in proposals if p["status"] == "ready"),
        "duplicate": sum(1 for p in proposals if p["status"] == "duplicate"),
        "needs_fix": sum(1 for p in proposals if p["status"] == "needs_fix"),
    }

    return {"proposals": proposals, "summary": summary}
```

### `_suggest_type(invalid_type, valid_types)`

Simple remap for common V1 type mismatches:

```python
_TYPE_REMAPS = {
    "lesson": "pill",
    "film": "movie",
    "show": "movie",
    "contact": "person",
    "journal": "daily",
    "todo": "task",
    "interest": "skill",
    "aspiration": "goal",
}

def _suggest_type(invalid_type: str, valid_types: set) -> str | None:
    remapped = _TYPE_REMAPS.get(invalid_type.lower())
    if remapped and remapped in valid_types:
        return remapped
    return "note"  # fallback suggestion
```

### `vault_service.import_accept(node_id, source_dir, type_override)`

```python
def import_accept(self, node_id: str, source_dir: str = "_backup",
                  type_override: str | None = None) -> dict:
    """Accept an import proposal — write the node to the vault."""
    # Find the source file
    backup_path = safe_resolve(Path(self.vault_path), source_dir)
    source_file = None
    for md_file in backup_path.rglob("*.md"):
        fm, _ = _parse_markdown(md_file)
        if fm.get("id", md_file.stem) == node_id:
            source_file = md_file
            break

    if not source_file:
        return {"error": f"Node '{node_id}' not found in {source_dir}", "status": 404}

    fm, content = _parse_markdown(source_file)
    node_type = type_override or fm.get("type", "note")

    # Strip V1 fields that don't belong
    fm.pop("updated", None)  # will be set fresh
    fm.pop("created", None)  # preserve if desired, or reset

    return self.write({
        "node_id": node_id,
        "title": fm.get("title", node_id),
        "type": node_type,
        "content": content,
        "frontmatter": fm,
        "edges": [],  # V1 nodes don't have structured edges
    })
```

### Routes

```python
@vault_bp.route("/api/vault/import", methods=["POST"])
def vault_import():
    data = request.json or {}
    source = data.get("source", "_backup")
    vault_service = current_app.config["vault_service"]
    result = vault_service.import_proposals(source)
    return jsonify(result)

@vault_bp.route("/api/vault/import/accept", methods=["POST"])
def vault_import_accept():
    data = request.json
    if not data or not data.get("node_id"):
        return jsonify({"error": "node_id required"}), 400
    vault_service = current_app.config["vault_service"]
    result = vault_service.import_accept(
        data["node_id"],
        data.get("source", "_backup"),
        data.get("type_override"),
    )
    status = result.pop("status", 200)
    if "error" in result:
        return jsonify(result), status
    return jsonify(result)
```

## What's NOT included

- **Frontend UI for import review** — this is API-only for now. The user can trigger imports via API/curl or we can add a UI later. The existing GraphUpdateCard pattern could be reused.
- **Batch accept** — accept one at a time. Keeps it simple, prevents accidental mass import.
- **Auto-edge inference** — V1 nodes don't have structured edges. Could be added later (scan content for `[[wikilinks]]` or use AI to suggest edges).

## Edge Cases

- **Source dir doesn't exist:** Return `{"proposals": [], "summary": {"total": 0, ...}}`
- **Duplicate already in vault:** Proposal has `status: "duplicate"` with match info. User can still force-accept (it'll be rejected by `write()` if the ID already exists).
- **Invalid type with no remap:** Suggested type defaults to `"note"`. User can override via `type_override`.
- **Non-markdown files in backup:** Ignored (only `*.md` files processed).

## Acceptance Criteria

1. `POST /api/vault/import` scans `_backup/` and returns proposals with validation status
2. Each proposal includes type validity, duplicate info, and suggested fixes
3. `POST /api/vault/import/accept` writes a single node using existing `vault_service.write()`
4. Invalid types get a suggested remap (e.g., `lesson` → `pill`)
5. Duplicates flagged with match type (exact ID vs semantic)
6. No nodes imported without explicit user acceptance
