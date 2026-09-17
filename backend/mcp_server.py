"""Athena MCP Server — knowledge graph tools for Claude."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading

from mcp.server.fastmcp import FastMCP

from schema_parser import parse_schema, get_edge_map
from vault_parser import VaultParser
from vault_graph import VaultGraph
from vector_search import VectorIndex, build_search_filter
from services.vault_service import VaultService
from permanence import get_permanence

# Logging to stderr (stdout is MCP transport)
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------

vault_path = os.getenv("VAULT_PATH", os.path.join(os.path.dirname(__file__), "..", "vault"))
if not os.path.isabs(vault_path):
    vault_path = os.path.abspath(vault_path)

schema_path = os.path.join(vault_path, "_meta", "schema.md")
schema = parse_schema(schema_path)

_parser = VaultParser(vault_path, edge_map=get_edge_map(schema))
graph = VaultGraph()
vector_index = VectorIndex(
    persist_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
)

_vault_lock = threading.RLock()


def _rebuild_all() -> dict:
    with _vault_lock:
        nodes, edges = _parser.parse()
        graph.build_from_parsed(nodes, edges)
        vector_index.rebuild(nodes)
        return graph.get_stats()


def _refresh_after_write(changed_node_id: str) -> dict:
    with _vault_lock:
        nodes, edges = _parser.parse()
        graph.build_from_parsed(nodes, edges)
        changed = next((n for n in nodes if n["id"] == changed_node_id), None)
        if changed:
            vector_index.upsert_one(changed)
        return graph.get_stats()


logger.info(f"Booting Athena MCP — vault at {vault_path}")
_rebuild_all()

vault_service = VaultService(
    vault_path, graph, vector_index, schema,
    _rebuild_all, lock=_vault_lock, refresh_fn=_refresh_after_write
)

logger.info("Boot complete")


# ---------------------------------------------------------------------------
# Schema validation helpers
# ---------------------------------------------------------------------------

def _parse_valid_statuses() -> dict[str, list[str]]:
    """Parse valid status values per type from schema.md comments."""
    import re
    result = {}
    schema_file = os.path.join(vault_path, "_meta", "schema.md")
    current_type = None
    with open(schema_file, "r") as f:
        for line in f:
            # Detect type headers like ### goal
            m = re.match(r'^###\s+(\w+)', line)
            if m:
                current_type = m.group(1)
            # Detect status lines with comments like: status: active  # active | paused | completed
            if current_type and line.strip().startswith("status:") and "#" in line:
                comment = line.split("#", 1)[1].strip()
                statuses = [s.strip() for s in comment.split("|") if s.strip()]
                if statuses:
                    result[current_type] = statuses
    return result


_VALID_STATUSES = _parse_valid_statuses()


def _validate_status(node_type: str, status: str) -> str | None:
    """Return error message if status is invalid for this type, else None."""
    valid = _VALID_STATUSES.get(node_type)
    if valid and status and status not in valid:
        return f"Invalid status '{status}' for type '{node_type}'. Valid: {valid}"
    return None


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("athena")


# ---------------------------------------------------------------------------
# Search tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_vault(query: str, n: int = 10, types: str = "", domains: str = "") -> str:
    """Semantic search over the vault knowledge graph. Returns ranked results with titles, types, and snippets.

    Args:
        query: Search query
        n: Max results (default 10)
        types: Comma-separated type filter (e.g. 'goal,habit')
        domains: Comma-separated domain filter (e.g. 'Self,People')
    """
    type_list = [t.strip() for t in types.split(",") if t.strip()] if types else None
    domain_list = [d.strip().lower() for d in domains.split(",") if d.strip()] if domains else None
    where = build_search_filter(types=type_list) if type_list else None

    results = vector_index.search(query, n=n, where=where)
    output = []
    for r in results:
        node = graph.get_node(r["id"])
        if not node:
            continue
        if domain_list and node.get("domain", "").lower() not in domain_list:
            continue
        output.append({
            "id": r["id"],
            "title": node.get("title", r["id"]),
            "type": node.get("type", "unknown"),
            "domain": node.get("domain", "unknown"),
            "status": node.get("status", ""),
            "score": round(r.get("score", 0), 3),
            "snippet": (node.get("content", "") or "")[:200],
        })

    return json.dumps(output, indent=2, default=str)


@mcp.tool()
async def list_nodes(type: str = "", domain: str = "", status: str = "") -> str:
    """List nodes filtered by type, domain, or status. Returns summaries without full content.

    Args:
        type: Filter by node type (e.g. 'goal')
        domain: Filter by domain (e.g. 'Self')
        status: Filter by status (e.g. 'active')
    """
    all_nodes = graph.get_all_nodes()
    results = []
    for node in all_nodes:
        if type and node.get("type") != type:
            continue
        if domain and node.get("domain", "").lower() != domain.lower():
            continue
        if status and node.get("status", "").lower() != status.lower():
            continue
        results.append({
            "id": node["id"],
            "title": node.get("title", node["id"]),
            "type": node.get("type", "unknown"),
            "domain": node.get("domain", "unknown"),
            "status": node.get("status", ""),
            "created": node.get("created", ""),
            "updated": node.get("updated", ""),
        })
    results.sort(key=lambda n: n.get("updated") or n.get("created") or "", reverse=True)
    return json.dumps(results, indent=2, default=str)


@mcp.tool()
async def get_activity(limit: int = 30) -> str:
    """Recent vault activity timeline — recently created or updated nodes.

    Args:
        limit: Max entries (default 30)
    """
    all_nodes = graph.get_all_nodes()
    entries = []
    for node in all_nodes:
        updated = node.get("updated") or node.get("created")
        if not updated:
            continue
        entries.append({
            "id": node["id"],
            "title": node.get("title", node["id"]),
            "type": node.get("type", "unknown"),
            "date": str(updated),
            "action": "updated" if node.get("updated") else "created",
        })
    entries.sort(key=lambda e: e["date"], reverse=True)
    return json.dumps(entries[:limit], indent=2, default=str)


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def read_node(node_id: str) -> str:
    """Read a single node with full content, frontmatter, and neighbors with edge types.

    Args:
        node_id: Node ID (filename without .md)
    """
    node = graph.get_node(node_id)
    if not node:
        return json.dumps({"error": f"Node '{node_id}' not found"})

    neighbors = []
    for neighbor_data in graph.get_neighbors_with_edges(node_id):
        neighbors.append({
            "id": neighbor_data["id"],
            "title": neighbor_data.get("title", neighbor_data["id"]),
            "type": neighbor_data.get("type", "unknown"),
            "edge_type": neighbor_data.get("_edge_type", "related"),
        })

    result = {
        "id": node["id"],
        "title": node.get("title", node["id"]),
        "type": node.get("type", "unknown"),
        "domain": node.get("domain", "unknown"),
        "status": node.get("status", ""),
        "content": node.get("content", ""),
        "frontmatter": {k: v for k, v in node.items()
                        if k not in ("id", "title", "type", "domain", "content", "file_path")},
        "neighbors": neighbors,
    }
    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def get_graph_stats() -> str:
    """Graph overview — total nodes, edges, and breakdowns by type and domain."""
    return json.dumps(graph.get_stats(), indent=2, default=str)


@mcp.tool()
async def get_schema() -> str:
    """The full vault schema — domains, types, edges, frontmatter fields, colours."""
    return json.dumps(schema, indent=2, default=str)


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def write_node(node_id: str, title: str, type: str, content: str = "", frontmatter: str = "{}", edges: str = "[]") -> str:
    """Create a new node in the vault. Returns filepath, stats, suggested links, and duplicate warnings.

    Args:
        node_id: Node ID (becomes filename)
        title: Node title
        type: Must be a valid schema type
        content: Markdown body
        frontmatter: JSON string of YAML frontmatter fields
        edges: JSON string array of wikilink target IDs
    """
    valid_types = schema.get("type_list", [])
    if valid_types and type not in valid_types:
        return json.dumps({"error": f"Invalid type '{type}'. Valid types: {valid_types}"})

    fm = json.loads(frontmatter) if isinstance(frontmatter, str) else frontmatter

    # Validate status against schema
    status_val = fm.get("status", "")
    if status_val:
        err = _validate_status(type, status_val)
        if err:
            return json.dumps({"error": err})
    edge_list = json.loads(edges) if isinstance(edges, str) else edges

    duplicate_warnings = []
    try:
        dupes = vector_index.find_duplicates(title, content, threshold=0.85)
        if dupes:
            duplicate_warnings = [{"id": d["id"], "title": d.get("title", d["id"]), "score": round(d["score"], 3)} for d in dupes[:3]]
    except Exception:
        pass

    permanence_warning = None
    level, _ = get_permanence(type)
    if level in ("identity", "fundamental"):
        permanence_warning = f"This is a {level}-level node. Changes to {level} nodes should be deliberate."

    result = vault_service.write({
        "node_id": node_id,
        "title": title,
        "type": type,
        "content": content,
        "frontmatter": fm,
        "edges": edge_list,
    })
    result["duplicate_warnings"] = duplicate_warnings
    if permanence_warning:
        result["permanence_warning"] = permanence_warning

    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def update_node(node_id: str, title: str = "", content: str = "", append_content: str = "",
                      frontmatter: str = "", add_tags: str = "", remove_tags: str = "",
                      add_edges: str = "", status: str = "") -> str:
    """Update an existing node. Returns cascade proposals for connected nodes affected by the change.

    Args:
        node_id: Node ID
        title: New title (empty = no change)
        content: Replace content (empty = no change)
        append_content: Append to content (empty = no change)
        frontmatter: JSON string of fields to merge
        add_tags: JSON string array of tags to add
        remove_tags: JSON string array of tags to remove
        add_edges: JSON string array of wikilink target IDs to add
        status: New status (empty = no change)
    """
    node = graph.get_node(node_id)
    if not node:
        return json.dumps({"error": f"Node '{node_id}' not found"})

    changes = {}
    if title:
        changes["title"] = title
    if content:
        changes["content"] = content
    if append_content:
        changes["append_content"] = append_content
    if frontmatter:
        changes["frontmatter"] = json.loads(frontmatter)
    if add_tags:
        changes["add_tags"] = json.loads(add_tags)
    if remove_tags:
        changes["remove_tags"] = json.loads(remove_tags)
    if add_edges:
        changes["add_edges"] = json.loads(add_edges)
    if status:
        changes.setdefault("frontmatter", {})["status"] = status

    # Validate status against schema
    node_type = node.get("type", "")
    if status:
        err = _validate_status(node_type, status)
        if err:
            return json.dumps({"error": err})

    if status == "superseded":
        fm = changes.get("frontmatter", {})
        if not fm.get("superseded_by"):
            return json.dumps({"error": "Cannot set status to 'superseded' without 'superseded_by' in frontmatter"})

    permanence_warning = None
    level, _ = get_permanence(node_type)
    if level in ("identity", "fundamental"):
        permanence_warning = f"Modifying a {level}-level node ({node.get('title', node_id)})."

    result = vault_service.update({"node_id": node_id, "changes": changes})
    if permanence_warning:
        result["permanence_warning"] = permanence_warning

    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def delete_node(node_id: str) -> str:
    """Archive a node to _backup/. Use for nodes that should be removed from the active graph.

    Args:
        node_id: Node ID
    """
    node = graph.get_node(node_id)
    if not node:
        return json.dumps({"error": f"Node '{node_id}' not found"})

    result = vault_service.delete(node_id)
    return json.dumps(result, indent=2, default=str)


# ---------------------------------------------------------------------------
# Graph tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def traverse_neighbors(node_id: str, depth: int = 1) -> str:
    """Multi-hop graph traversal from a node. Returns neighbors grouped by hop distance. Max depth 3.

    Args:
        node_id: Starting node ID
        depth: Hop depth (1-3, default 1)
    """
    node = graph.get_node(node_id)
    if not node:
        return json.dumps({"error": f"Node '{node_id}' not found"})

    depth = min(depth, 3)
    result = {"root": {"id": node_id, "title": node.get("title", node_id), "type": node.get("type", "")}}
    visited = {node_id}
    current_layer = [node_id]

    for hop in range(1, depth + 1):
        next_layer = []
        hop_results = []
        for nid in current_layer:
            for neighbor_data in graph.get_neighbors_with_edges(nid):
                neighbor_id = neighbor_data["id"]
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                next_layer.append(neighbor_id)
                hop_results.append({
                    "id": neighbor_id,
                    "title": neighbor_data.get("title", neighbor_id),
                    "type": neighbor_data.get("type", "unknown"),
                    "edge_type": neighbor_data.get("_edge_type", "related"),
                    "via": nid,
                })
        result[f"hop_{hop}"] = hop_results
        current_layer = next_layer

    return json.dumps(result, indent=2, default=str)


@mcp.tool()
async def find_cross_references(node_id: str) -> str:
    """Suggest potential links for a node based on content similarity.

    Args:
        node_id: Node ID
    """
    node = graph.get_node(node_id)
    if not node:
        return json.dumps({"error": f"Node '{node_id}' not found"})

    content = node.get("content", "") or node.get("title", "")
    if not content:
        return json.dumps({"suggestions": []})

    results = vector_index.search(content, n=10)
    existing = {n["id"] for n in graph.get_neighbors_with_edges(node_id)}
    suggestions = []
    for r in results:
        if r["id"] == node_id or r["id"] in existing:
            continue
        target = graph.get_node(r["id"])
        if target:
            suggestions.append({
                "target_id": r["id"],
                "target_title": target.get("title", r["id"]),
                "target_type": target.get("type", "unknown"),
                "score": round(r.get("score", 0), 3),
            })

    return json.dumps({"suggestions": suggestions[:5]}, indent=2, default=str)


# ---------------------------------------------------------------------------
# Analysis tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def detect_conflicts(message: str) -> str:
    """Check a user message against existing graph for contradictions. Call when user expresses intentions.

    Args:
        message: The user's message or stated intention
    """
    from services.conflict_service import detect_conflicts as _detect
    conflicts = _detect(message, graph, vector_index)
    return json.dumps({"conflicts": conflicts, "count": len(conflicts)}, indent=2, default=str)


@mcp.tool()
async def check_accountability() -> str:
    """Get habit streaks, overdue commitments, and fundamentals status. Call at start of conversations."""
    from services.accountability_service import calculate_streaks, find_overdue_commitments, check_fundamentals
    return json.dumps({
        "streaks": calculate_streaks(graph),
        "overdue": find_overdue_commitments(graph),
        "fundamentals": check_fundamentals(graph),
    }, indent=2, default=str)


@mcp.tool()
async def check_relationships(lookback_days: int = 30) -> str:
    """Relationship health across tracked people — mention frequency, health score, drift alerts.

    Args:
        lookback_days: Analysis window in days (default 30)
    """
    from services.relationship_service import assess_relationship_health
    health = assess_relationship_health(graph, lookback_days=lookback_days)
    return json.dumps({"relationships": health, "lookback_days": lookback_days}, indent=2, default=str)


@mcp.tool()
async def audit_vault() -> str:
    """Structural health scan — stale statuses, orphan nodes, broken wikilinks, type mismatches."""
    from services.audit_service import audit_vault as _audit
    issues = _audit(graph, schema, vault_path)
    return json.dumps(issues, indent=2, default=str)


# ---------------------------------------------------------------------------
# Admin tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def rebuild_vault() -> str:
    """Force full re-parse of vault and re-index vectors. Use after manual vault edits."""
    stats = _rebuild_all()
    return json.dumps({"ok": True, "stats": stats}, indent=2, default=str)


@mcp.tool()
async def vault_repair() -> str:
    """Fix structural issues — duplicate sections, heading problems, broken formatting."""
    result = vault_service.repair()
    return json.dumps(result, indent=2, default=str)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
