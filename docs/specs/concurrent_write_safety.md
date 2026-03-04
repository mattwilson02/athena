# Spec: Write Latency & Incremental Indexing

**Epoch:** E1 Ground Truth — Item 5 (revised)
**Files:** `backend/server.py`, `backend/services/vault_service.py`, `backend/vector_search.py`

---

## What

Fix vault write latency by replacing the full rebuild-on-every-write with incremental updates.

---

## Problem

Every `write()` and `update()` call triggers `rebuild_all()`:

```python
def rebuild_all() -> dict:
    with vault_lock:
        nodes, edges = parser.parse()       # walk + read ALL ~96 files
        graph.build_from_parsed(nodes, edges) # rebuild entire NetworkX graph
        vector_index.rebuild(nodes)           # DELETE collection + re-embed ALL nodes
        return graph.get_stats()
```

`vector_index.rebuild()` is the bottleneck — it **deletes the entire ChromaDB collection** and re-embeds every node through the local embedding model. ~2-5 seconds per write.

The RLock serializes all writes, so accepting 3 graph updates = 3 sequential full rebuilds = 6-15 seconds of blocking. The concurrency safety from E1 is correct but masks the real issue: each write does O(N) work when it should do O(1).

---

## Implementation

### 1. Add incremental methods to `VectorIndex`

```python
def upsert_one(self, node: dict) -> None:
    """Upsert a single node into the index without rebuilding."""
    # Same document-building logic as index_all(), but for one node.

def delete_one(self, node_id: str) -> None:
    """Remove a single node from the index."""
    self.collection.delete(ids=[node_id])
```

### 2. Add `refresh_after_write()` to server.py

```python
def refresh_after_write(changed_file: str) -> dict:
    """Re-parse vault, rebuild graph, upsert only changed node into vector index."""
    with vault_lock:
        nodes, edges = parser.parse()
        graph.build_from_parsed(nodes, edges)
        # Find the node that changed and upsert just that one
        changed_node = next((n for n in nodes if changed_file.endswith(f"{n['id']}.md")), None)
        if changed_node:
            vector_index.upsert_one(changed_node)
        return graph.get_stats()
```

Full vault parse + graph rebuild is fast (~50ms for 96 files). The expensive part — vector embedding — now only processes one node.

### 3. Update VaultService

Pass both `rebuild_fn` (boot/explicit) and `refresh_fn` (per-write) to VaultService. `write()` and `update()` call `refresh_fn(filepath)` instead of `rebuild_fn()`. `repair()` and explicit rebuild endpoint still use `rebuild_fn`.

---

## What stays the same

- `rebuild_all()` still exists for boot and `POST /api/vault/rebuild`
- The RLock stays — still need serialized access to graph state
- Full vault parse stays in the per-write path — it's fast and keeps the graph consistent
- `find_cross_references()` still runs after writes

---

## Tests

- Existing tests should pass unchanged (they use `rebuild_fn` which still works)
- Manual test: accept 3 graph updates rapidly — should feel near-instant instead of 6-15s

---

## Verification

1. Accept a graph update → response time < 500ms (was 2-5s)
2. Accept 3 updates rapidly → all succeed, no perceptible lag
3. `POST /api/vault/rebuild` still does a full rebuild (boot-time behavior)
4. `cd backend && python3 -m pytest tests/ -v` — all green
