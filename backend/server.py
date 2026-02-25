"""Flask API entry point for Athena."""

from __future__ import annotations

import json
import os
import re
import logging

import yaml
import anthropic
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

from schema_parser import parse_schema, get_folder_for_type, get_edge_map
from vault_parser import VaultParser
from vault_graph import VaultGraph
from vector_search import VectorIndex
from mentor_agent import MentorAgent, load_insights_prompt
from chat_store import ChatStore

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Resolve vault path
vault_path = os.getenv("VAULT_PATH", "../vault")
vault_path = os.path.abspath(os.path.join(os.path.dirname(__file__), vault_path))

# Parse schema (single source of truth for types, domains, edges)
schema_path = os.path.join(vault_path, "_meta", "schema.md")
schema = parse_schema(schema_path)

# Core components
parser = VaultParser(vault_path, edge_map=get_edge_map(schema))
graph = VaultGraph()
vector_index = VectorIndex(
    persist_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
)
chat_store = ChatStore(
    store_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_sessions")
)


def rebuild_all() -> dict:
    """Parse vault, rebuild graph and vector index. Returns stats."""
    nodes, edges = parser.parse()
    graph.build_from_parsed(nodes, edges)
    vector_index.rebuild(nodes)
    return graph.get_stats()


# --- Boot ---
logger.info(f"Booting Athena — vault at {vault_path}")
rebuild_all()

# Mentor agent (only initialized if API key is set)
mentor: MentorAgent | None = None
if os.getenv("ANTHROPIC_API_KEY"):
    mentor = MentorAgent(graph, vector_index, schema, vault_path=vault_path)
    logger.info("Mentor agent ready")
else:
    logger.warning("ANTHROPIC_API_KEY not set — chat and insights endpoints disabled")


# --- Session Endpoints ---


@app.route("/api/chat/sessions", methods=["GET"])
def list_sessions():
    """List all chat sessions."""
    return jsonify({"sessions": chat_store.list_sessions()})


@app.route("/api/chat/sessions", methods=["POST"])
def create_session():
    """Create a new chat session."""
    session = chat_store.create_session()
    return jsonify(session), 201


@app.route("/api/chat/sessions/<session_id>", methods=["GET"])
def get_session(session_id: str):
    """Get a session with all messages."""
    session = chat_store.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(session)


@app.route("/api/chat/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id: str):
    """Delete a chat session."""
    if not chat_store.delete_session(session_id):
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/chat/sessions/<session_id>/dismiss", methods=["POST"])
def dismiss_update(session_id: str):
    """Persist a dismissed graph update so it stays dismissed on reload."""
    data = request.json or {}
    update_key = data.get("update_key")
    if not update_key:
        return jsonify({"error": "update_key is required"}), 400
    if not chat_store.dismiss_update(session_id, update_key):
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"ok": True})


# --- Dedup Helper ---


def _dedup_check(updates: list[dict]) -> list[dict]:
    """Check proposed create actions for potential duplicates.

    For each 'create' action, searches for existing nodes with similar titles.
    Annotates the update with match info so the frontend can offer merge/create options.
    """
    enriched = []
    for update in updates:
        if update.get("action") != "create":
            enriched.append(update)
            continue

        node_id = update.get("node_id", "")
        title = update.get("title", "")
        node_type = update.get("type", "")

        # Check for existing node with exact same ID
        existing = graph.get_node(node_id)
        if existing:
            # Exact ID match — convert to update suggestion
            update["_duplicate"] = {
                "match": "exact_id",
                "existing_id": existing["id"],
                "existing_title": existing.get("title", existing["id"]),
                "existing_type": existing.get("type", "unknown"),
            }
            enriched.append(update)
            continue

        # Semantic search for similar nodes
        matches = vector_index.find_duplicates(node_id, title, node_type)
        if matches:
            update["_duplicate"] = {
                "match": matches[0]["match"],
                "existing_id": matches[0]["id"],
                "existing_title": matches[0]["title"],
                "existing_type": matches[0]["type"],
                "score": matches[0].get("score", 0),
                "alternatives": matches[1:] if len(matches) > 1 else [],
            }

        enriched.append(update)

    return enriched


# --- Chat Endpoint ---


@app.route("/api/chat", methods=["POST"])
def chat():
    """Send a message, get AI response + graph update proposals."""
    if mentor is None:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured"}), 503

    data = request.json
    if not data or not data.get("message", "").strip():
        return jsonify({"error": "message is required"}), 400

    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    # Verify session exists
    session = chat_store.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    message = data["message"]

    # Save user message
    chat_store.append_message(session_id, {"role": "user", "content": message})

    # Get conversation history for Claude (role + content only)
    history = chat_store.get_messages_for_api(session_id)
    # Remove the last message (the one we just added) — mentor.chat() adds it itself
    history = history[:-1]

    try:
        result = mentor.chat(message, history)
    except anthropic.AuthenticationError:
        return jsonify({"error": "Invalid API key. Check your ANTHROPIC_API_KEY."}), 401
    except anthropic.RateLimitError:
        return jsonify({"error": "Rate limited. Slow down and try again shortly."}), 429
    except anthropic.APIStatusError as e:
        if e.status_code == 529:
            return jsonify({"error": "Claude is overloaded right now. Try again in a few seconds."}), 529
        logger.error(f"Claude API error: {e}")
        return jsonify({"error": "Something went wrong talking to Claude. Try again."}), 502
    except anthropic.APIError as e:
        logger.error(f"Claude API error: {e}")
        return jsonify({"error": "Something went wrong talking to Claude. Try again."}), 502

    # Post-process: dedup check on create proposals
    graph_updates = _dedup_check(result["graph_updates"])

    # Save assistant message (with full response for Claude context continuity)
    chat_store.append_message(session_id, {
        "role": "assistant",
        "content": result["full_response"],
        "graph_updates": graph_updates,
        "relevant_nodes": result["relevant_nodes"],
    })

    return jsonify({
        "response": result["response"],
        "graph_updates": graph_updates,
        "relevant_nodes": result["relevant_nodes"],
    })


# --- Insights Endpoint ---


@app.route("/api/insights", methods=["GET"])
def insights():
    """Proactive AI observations about the knowledge graph."""
    if mentor is None:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured"}), 503

    stats = graph.get_stats()
    if stats["total_nodes"] == 0:
        return jsonify({"insights": "Your vault is empty. Add some nodes first and I'll find patterns."})

    # Build a concise graph summary for Claude
    all_nodes = graph.get_all_nodes()
    summary_parts = []
    for node in all_nodes:
        neighbors = graph.get_neighbors(node["id"])
        neighbor_names = [f"{n['title']} ({n['type']})" for n in neighbors]
        connected = f" [connected to: {', '.join(neighbor_names)}]" if neighbor_names else ""
        summary_parts.append(f"- {node.get('title', node['id'])} ({node.get('type', '?')}){connected}")

    graph_summary = "\n".join(summary_parts)

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=1024,
            system=load_insights_prompt(vault_path),
            messages=[{"role": "user", "content": f"Here is my knowledge graph:\n{graph_summary}"}],
        )
        return jsonify({"insights": response.content[0].text})
    except anthropic.APIError as e:
        return jsonify({"error": f"Claude API error: {e}"}), 502


# --- Graph Endpoints ---


@app.route("/api/graph", methods=["GET"])
def get_graph():
    """Return all nodes and edges."""
    return jsonify({
        "nodes": graph.get_all_nodes(),
        "edges": graph.get_all_edges(),
    })


@app.route("/api/graph/stats", methods=["GET"])
def get_graph_stats():
    """Return node counts and type breakdown."""
    return jsonify(graph.get_stats())


@app.route("/api/node/<node_id>", methods=["GET"])
def get_node(node_id: str):
    """Return a single node and its neighbors."""
    node = graph.get_node(node_id)
    if node is None:
        return jsonify({"error": "Node not found"}), 404
    neighbors = graph.get_neighbors(node_id)
    return jsonify({"node": node, "neighbors": neighbors})


@app.route("/api/nodes", methods=["GET"])
def get_nodes_by_type():
    """Filter nodes by type or domain."""
    node_type = request.args.get("type")
    domain = request.args.get("domain")

    if node_type:
        return jsonify({"nodes": graph.get_nodes_by_type(node_type)})

    if domain:
        # Get all types in this domain from schema
        domain_info = schema["domains"].get(domain)
        if not domain_info:
            return jsonify({"error": f"Unknown domain '{domain}'"}), 400
        domain_types = domain_info.get("types", [])
        nodes = []
        for dt in domain_types:
            nodes.extend(graph.get_nodes_by_type(dt))
        return jsonify({"nodes": nodes})

    return jsonify({"nodes": graph.get_all_nodes()})


@app.route("/api/schema", methods=["GET"])
def get_schema():
    """Return the parsed schema — domains, types, frontmatter fields."""
    return jsonify(schema)


@app.route("/api/search", methods=["GET"])
def search():
    """Semantic search across vault."""
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Missing query parameter 'q'"}), 400
    results = vector_index.search(query)
    return jsonify({"results": results})


# --- Vault Endpoints ---


@app.route("/api/vault/write", methods=["POST"])
def vault_write():
    """Write a node to the vault as a markdown file."""
    data = request.json
    if not data:
        return jsonify({"error": "Request body required"}), 400

    node_id = data.get("node_id")
    title = data.get("title", node_id)
    node_type = data.get("type")
    content = data.get("content", "")
    frontmatter = data.get("frontmatter", {})

    if not node_id:
        return jsonify({"error": "node_id is required"}), 400

    # Sanitize node_id — normalize to lowercase alphanumeric with hyphens
    node_id = node_id.lower().replace("_", "-").replace(" ", "-")
    node_id = re.sub(r"[^a-z0-9-]", "", node_id)
    node_id = re.sub(r"-+", "-", node_id).strip("-")
    if not node_id:
        return jsonify({"error": "node_id is empty after sanitization"}), 400

    # Validate type against schema
    valid_types = set(schema.get("type_list", []))
    if node_type and node_type not in valid_types:
        # Try to find closest valid type before rejecting
        logger.warning(f"Invalid node type '{node_type}' for node '{node_id}' — rejecting")
        return jsonify({"error": f"Invalid type '{node_type}'. Valid types: {', '.join(sorted(valid_types))}"}), 400

    # Resolve folder from schema (falls back to client-provided folder)
    folder = get_folder_for_type(schema, node_type) or data.get("folder")
    if not folder:
        return jsonify({"error": f"Unknown type '{node_type}' and no folder provided"}), 400

    # Prevent path traversal
    if ".." in folder or folder.startswith("/"):
        return jsonify({"error": "Invalid folder path"}), 400

    # Ensure frontmatter has core fields (use sanitized node_id)
    frontmatter["id"] = node_id
    frontmatter.setdefault("type", node_type)
    frontmatter.setdefault("title", title)

    # Build markdown
    fm_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
    body = f"# {title}\n\n{content}\n"

    # Write edges as wikilinks under section headings
    edges = data.get("edges", [])
    if edges:
        # Group edges by section
        sections: dict[str, list[str]] = {}
        for edge in edges:
            target = edge.get("target", "")
            edge_type = edge.get("type", "relates_to")
            if target:
                # Sanitize target ID the same way
                target = target.lower().replace("_", "-").replace(" ", "-")
                target = re.sub(r"[^a-z0-9-]", "", target)
                target = re.sub(r"-+", "-", target).strip("-")
                section = _edge_type_to_section(edge_type)
                sections.setdefault(section, []).append(target)

        for section_name, targets in sections.items():
            body += f"\n## {section_name}\n"
            for t in targets:
                body += f"- [[{t}]]\n"

    md = f"---\n{fm_str}---\n\n{body}"

    # Write file
    filepath = os.path.join(vault_path, folder, f"{node_id}.md")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md)

    stats = rebuild_all()

    # Cross-reference: find suggested links for the new node
    suggested_links = _find_cross_references(node_id)

    return jsonify({
        "ok": True,
        "filepath": f"{folder}/{node_id}.md",
        "stats": stats,
        "suggested_links": suggested_links,
    })


@app.route("/api/vault/update", methods=["POST"])
def vault_update():
    """Update an existing node — patch frontmatter, append content, add/remove edges."""
    data = request.json
    if not data:
        return jsonify({"error": "Request body required"}), 400

    node_id = data.get("node_id")
    if not node_id:
        return jsonify({"error": "node_id is required"}), 400

    # Sanitize node_id the same way as vault/write
    node_id = node_id.lower().replace("_", "-").replace(" ", "-")
    node_id = re.sub(r"[^a-z0-9-]", "", node_id)
    node_id = re.sub(r"-+", "-", node_id).strip("-")

    # Find the existing node to get its filepath
    node = graph.get_node(node_id)
    if node is None:
        return jsonify({"error": f"Node '{node_id}' not found"}), 404

    filepath = os.path.join(vault_path, node["filepath"])
    if not os.path.isfile(filepath):
        return jsonify({"error": f"File not found for node '{node_id}'"}), 404

    # Read current file
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()

    fm, body = _split_frontmatter(raw)
    changes = data.get("changes", {})

    # Replace title if provided
    new_title = changes.get("title")
    if new_title:
        fm["title"] = new_title
        # Update the # heading in body
        body = re.sub(r"^# .+", f"# {new_title}", body, count=1)

    # Replace content if provided (replaces body text, keeps section headings with wikilinks)
    new_content = changes.get("content")
    if new_content:
        # Preserve edge sections (## headings with wikilinks) but replace everything before them
        section_match = re.search(r"\n## ", body)
        if section_match:
            heading_match = re.match(r"# .+\n", body)
            heading = heading_match.group(0) if heading_match else f"# {fm.get('title', node_id)}\n"
            body = heading + "\n" + new_content + "\n" + body[section_match.start():]
        else:
            heading_match = re.match(r"# .+\n", body)
            heading = heading_match.group(0) if heading_match else f"# {fm.get('title', node_id)}\n"
            body = heading + "\n" + new_content + "\n"

    # Patch frontmatter
    fm_updates = changes.get("frontmatter", {})
    if fm_updates:
        fm.update(fm_updates)

    from datetime import date
    fm["updated"] = date.today().isoformat()

    # Add tags
    add_tags = changes.get("add_tags", [])
    if add_tags:
        existing_tags = fm.get("tags", [])
        if not isinstance(existing_tags, list):
            existing_tags = [existing_tags] if existing_tags else []
        for tag in add_tags:
            if tag not in existing_tags:
                existing_tags.append(tag)
        fm["tags"] = existing_tags

    # Remove tags
    remove_tags = changes.get("remove_tags", [])
    if remove_tags and isinstance(fm.get("tags"), list):
        fm["tags"] = [t for t in fm["tags"] if t not in remove_tags]

    # Append content
    append_content = changes.get("append_content", "")
    if append_content:
        # Insert before the first ## section heading, or at the end
        section_match = re.search(r"\n## ", body)
        if section_match:
            pos = section_match.start()
            body = body[:pos] + "\n\n" + append_content + body[pos:]
        else:
            body = body.rstrip() + "\n\n" + append_content + "\n"

    # Add edges (wikilinks under appropriate sections)
    for edge in changes.get("add_edges", []):
        target = edge.get("target", "")
        edge_type = edge.get("type", "relates_to")
        if target:
            # Sanitize target ID
            target = target.lower().replace("_", "-").replace(" ", "-")
            target = re.sub(r"[^a-z0-9-]", "", target)
            target = re.sub(r"-+", "-", target).strip("-")
            section = _edge_type_to_section(edge_type)
            body = _add_wikilink_to_section(body, section, target)

    # Write updated file
    fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"---\n{fm_str}---\n{body}")

    stats = rebuild_all()
    return jsonify({"ok": True, "node_id": node_id, "stats": stats})


def _split_frontmatter(content: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns (dict, body_string)."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content
    try:
        fm = yaml.safe_load(content[3:end])
        if not isinstance(fm, dict):
            return {}, content
        return fm, content[end + 3:]
    except yaml.YAMLError:
        return {}, content


def _edge_type_to_section(edge_type: str) -> str:
    """Map edge type back to a section heading name."""
    reverse = {
        "blocked_by": "Blockers", "supported_by": "Supports", "relates_to": "Related",
        "contradicts": "Contradicts", "inspired_by": "Inspired By", "involves": "People",
        "part_of": "Part Of", "located_in": "Located In", "funded_by": "Funded By",
        "met_at": "Met At",
    }
    return reverse.get(edge_type, "Related")


def _add_wikilink_to_section(body: str, section_name: str, target_id: str) -> str:
    """Add a [[wikilink]] under a section heading. Creates section if missing."""
    if f"[[{target_id}]]" in body:
        return body

    wikilink = f"- [[{target_id}]]"
    pattern = re.compile(rf"^## {re.escape(section_name)}\s*$", re.MULTILINE)
    match = pattern.search(body)

    if match:
        # Insert after heading + any comment lines
        insert_pos = match.end()
        rest = body[insert_pos:]
        lines = rest.split("\n")
        skip = 0
        for line in lines:
            s = line.strip()
            if s == "" or s.startswith("<!--") or s.endswith("-->"):
                skip += 1
            else:
                break
        insert_pos += sum(len(lines[i]) + 1 for i in range(skip))
        body = body[:insert_pos] + wikilink + "\n" + body[insert_pos:]
    else:
        body = body.rstrip() + f"\n\n## {section_name}\n{wikilink}\n"

    return body


@app.route("/api/vault/rebuild", methods=["POST"])
def vault_rebuild():
    """Force rebuild of graph and vector indexes."""
    stats = rebuild_all()
    return jsonify({"ok": True, "stats": stats})


# --- Smart Linking ---


def _find_cross_references(node_id: str, max_suggestions: int = 5) -> list[dict]:
    """Find potential links for a node by scanning existing nodes.

    Three strategies:
    1. Reverse scan — existing nodes whose content mentions this node's title
    2. Forward scan — this node's content mentions existing node titles
    3. Semantic similarity — related nodes not yet linked

    Returns [{source, target, type, reason}].
    """
    node = graph.get_node(node_id)
    if node is None:
        return []

    title = node.get("title", "")
    node_type = node.get("type", "")
    content = node.get("content", "")
    existing_neighbors = {n["id"] for n in graph.get_neighbors(node_id, depth=1)}
    suggestions: list[dict] = []
    seen_targets: set[str] = set()

    all_nodes = graph.get_all_nodes()

    # 1. Reverse scan: existing nodes that mention this node
    if title:
        title_lower = title.lower()
        for other in all_nodes:
            if other["id"] == node_id or other["id"] in existing_neighbors:
                continue
            other_content = other.get("content", "")
            other_title = other.get("title", "")
            if title_lower in other_content.lower() or title_lower in other_title.lower():
                if other["id"] not in seen_targets:
                    suggestions.append({
                        "source": other["id"],
                        "target": node_id,
                        "type": _infer_edge_type(other.get("type", ""), node_type),
                        "reason": f'"{other.get("title", other["id"])}" mentions "{title}"',
                    })
                    seen_targets.add(other["id"])

    # 2. Forward scan: this node mentions existing node titles
    if content:
        content_lower = content.lower()
        for other in all_nodes:
            if other["id"] == node_id or other["id"] in existing_neighbors:
                continue
            if other["id"] in seen_targets:
                continue
            other_title = other.get("title", "")
            if other_title and len(other_title) > 2 and other_title.lower() in content_lower:
                suggestions.append({
                    "source": node_id,
                    "target": other["id"],
                    "type": _infer_edge_type(node_type, other.get("type", "")),
                    "reason": f'Content mentions "{other_title}"',
                })
                seen_targets.add(other["id"])

    # 3. Semantic similarity
    try:
        search_text = f"{title} {content[:200]}" if content else title
        similar = vector_index.search(search_text, n=8)
        for result in similar:
            rid = result["id"]
            if rid == node_id or rid in existing_neighbors or rid in seen_targets:
                continue
            if result.get("score", 999) > 0.8:
                continue
            suggestions.append({
                "source": node_id,
                "target": rid,
                "type": _infer_edge_type(node_type, result.get("type", "")),
                "reason": f'Semantically related to "{result.get("title", rid)}"',
            })
            seen_targets.add(rid)
    except Exception as e:
        logger.warning(f"Semantic search failed during cross-ref: {e}")

    return suggestions[:max_suggestions]


def _infer_edge_type(source_type: str, target_type: str) -> str:
    """Infer a sensible default edge type from source and target node types."""
    if target_type == "person" or source_type == "person":
        return "involves"
    if target_type == "place" or source_type == "place":
        return "located_in"
    if target_type == "project" or source_type == "project":
        return "part_of"
    if target_type == "budget" or source_type == "budget":
        return "funded_by"
    if target_type == "book" or target_type == "article":
        return "inspired_by"
    return "relates_to"


@app.route("/api/graph/suggest-links", methods=["POST"])
def suggest_links():
    """Suggest missing edges — for a specific node or the full graph.

    Body: {"node_id": "optional-id"}
    If node_id given: fast heuristic scan.
    If omitted: AI analyses the full graph (requires API key).
    """
    data = request.json or {}
    node_id = data.get("node_id")

    if node_id:
        suggestions = _find_cross_references(node_id)
        return jsonify({"suggestions": suggestions})

    # Full graph AI analysis
    if mentor is None:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured"}), 503

    stats = graph.get_stats()
    if stats["total_nodes"] < 2:
        return jsonify({"suggestions": []})

    all_nodes = graph.get_all_nodes()
    all_edges = graph.get_all_edges()

    node_lines = []
    for n in all_nodes:
        degree = graph.get_degree(n["id"])
        node_lines.append(
            f'- {n["id"]} ({n.get("type", "?")}) "{n.get("title", "")}" [degree={degree}]'
        )

    edge_lines = [f'- {e["source"]} --{e["type"]}--> {e["target"]}' for e in all_edges]

    prompt = (
        f"Nodes ({len(all_nodes)}):\n" + "\n".join(node_lines)
        + f"\n\nEdges ({len(all_edges)}):\n"
        + ("\n".join(edge_lines) if edge_lines else "(none)")
        + "\n\nSuggest up to 10 missing edges. Focus on: orphaned nodes (degree=0), "
        "nodes that clearly relate but aren't linked, cross-domain connections.\n\n"
        "Return ONLY a JSON array:\n"
        '[{"source": "node-id", "target": "node-id", "type": "edge_type", "reason": "why"}]'
    )

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=1024,
            system="You are a graph analysis tool. Return only valid JSON arrays.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        json_match = re.search(r"\[.*\]", raw, re.DOTALL)
        if json_match:
            suggestions = json.loads(json_match.group())
            valid_ids = {n["id"] for n in all_nodes}
            suggestions = [
                s for s in suggestions
                if isinstance(s, dict)
                and s.get("source") in valid_ids
                and s.get("target") in valid_ids
            ]
            return jsonify({"suggestions": suggestions})
        return jsonify({"suggestions": []})
    except (anthropic.APIError, json.JSONDecodeError) as e:
        logger.error(f"suggest-links error: {e}")
        return jsonify({"error": str(e)}), 502


# --- Error Handlers ---


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(Exception)
def handle_error(e):
    logger.error(f"Unhandled error: {e}", exc_info=True)
    return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5001)
