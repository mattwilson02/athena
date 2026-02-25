"""Flask API entry point for Athena."""

import os
import re
import logging

import yaml
import anthropic
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

from vault_parser import VaultParser
from vault_graph import VaultGraph
from vector_search import VectorIndex
from mentor_agent import MentorAgent

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Resolve vault path
vault_path = os.getenv("VAULT_PATH", "../vault")
vault_path = os.path.abspath(os.path.join(os.path.dirname(__file__), vault_path))

# Core components
parser = VaultParser(vault_path)
graph = VaultGraph()
vector_index = VectorIndex(
    persist_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
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
    mentor = MentorAgent(graph, vector_index)
    logger.info("Mentor agent ready")
else:
    logger.warning("ANTHROPIC_API_KEY not set — chat and insights endpoints disabled")


# --- Chat Endpoints ---


@app.route("/api/chat", methods=["POST"])
def chat():
    """Send a message, get AI response + graph update proposals."""
    if mentor is None:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured"}), 503

    data = request.json
    if not data or not data.get("message", "").strip():
        return jsonify({"error": "message is required"}), 400

    try:
        result = mentor.chat(data["message"])
        return jsonify(result)
    except anthropic.AuthenticationError:
        return jsonify({"error": "Invalid ANTHROPIC_API_KEY"}), 401
    except anthropic.APIError as e:
        return jsonify({"error": f"Claude API error: {e}"}), 502


@app.route("/api/chat/reset", methods=["POST"])
def chat_reset():
    """Clear conversation history."""
    if mentor:
        mentor.reset()
    return jsonify({"ok": True})


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
    """Filter nodes by type."""
    node_type = request.args.get("type")
    if not node_type:
        return jsonify({"nodes": graph.get_all_nodes()})
    return jsonify({"nodes": graph.get_nodes_by_type(node_type)})


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
    folder = data.get("folder")
    content = data.get("content", "")
    frontmatter = data.get("frontmatter", {})

    if not node_id or not folder:
        return jsonify({"error": "node_id and folder are required"}), 400

    # Sanitize node_id — only allow lowercase alphanumeric and hyphens
    if not re.match(r"^[a-z0-9-]+$", node_id):
        return jsonify({"error": "node_id must be lowercase alphanumeric with hyphens"}), 400

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
