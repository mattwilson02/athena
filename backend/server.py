"""Flask API entry point for Athena."""

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
from mentor_agent import MentorAgent
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
    mentor = MentorAgent(graph, vector_index, schema)
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
        return jsonify({"error": "Invalid ANTHROPIC_API_KEY"}), 401
    except anthropic.APIError as e:
        return jsonify({"error": f"Claude API error: {e}"}), 502

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
            system=(
                "You are Athena, analyzing a personal knowledge graph. "
                "Identify 3-5 actionable observations: blocked goals, "
                "contradictions between beliefs and actions, unsupported goals, "
                "patterns, or blind spots. Be specific and reference nodes by name. "
                "Be concise."
            ),
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

    # Sanitize node_id — only allow lowercase alphanumeric and hyphens
    if not re.match(r"^[a-z0-9-]+$", node_id):
        return jsonify({"error": "node_id must be lowercase alphanumeric with hyphens"}), 400

    # Resolve folder from schema (falls back to client-provided folder)
    folder = get_folder_for_type(schema, node_type) or data.get("folder")
    if not folder:
        return jsonify({"error": f"Unknown type '{node_type}' and no folder provided"}), 400

    # Prevent path traversal
    if ".." in folder or folder.startswith("/"):
        return jsonify({"error": "Invalid folder path"}), 400

    # Ensure frontmatter has core fields
    frontmatter.setdefault("id", node_id)
    frontmatter.setdefault("type", node_type)
    frontmatter.setdefault("title", title)

    # Build markdown
    fm_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
    md = f"---\n{fm_str}---\n\n# {title}\n\n{content}\n"

    # Write file
    filepath = os.path.join(vault_path, folder, f"{node_id}.md")
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md)

    stats = rebuild_all()
    return jsonify({"ok": True, "filepath": f"{folder}/{node_id}.md", "stats": stats})


@app.route("/api/vault/update", methods=["POST"])
def vault_update():
    """Update an existing node — patch frontmatter, append content, add/remove edges."""
    data = request.json
    if not data:
        return jsonify({"error": "Request body required"}), 400

    node_id = data.get("node_id")
    if not node_id:
        return jsonify({"error": "node_id is required"}), 400

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


# --- Error Handlers ---


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(Exception)
def handle_error(e):
    logger.error(f"Unhandled error: {e}", exc_info=True)
    return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
