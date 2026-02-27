"""Flask API entry point for Athena — thin app factory."""

from __future__ import annotations

import logging
import os

from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

from schema_parser import parse_schema, get_edge_map
from vault_parser import VaultParser
from vault_graph import VaultGraph
from vector_search import VectorIndex
from mentor_agent import MentorAgent
from chat_store import ChatStore
from services.vault_service import VaultService
from services.chat_service import ChatService
from routes.chat_routes import chat_bp
from routes.graph_routes import graph_bp
from routes.vault_routes import vault_bp
from routes.insights_routes import insights_bp

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)

    # Resolve vault path
    vault_path = os.getenv("VAULT_PATH", "../vault")
    vault_path = os.path.abspath(os.path.join(os.path.dirname(__file__), vault_path))

    # Parse schema
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
        nodes, edges = parser.parse()
        graph.build_from_parsed(nodes, edges)
        vector_index.rebuild(nodes)
        return graph.get_stats()

    # Boot
    logger.info(f"Booting Athena — vault at {vault_path}")
    rebuild_all()

    # Mentor agent
    mentor = None
    if os.getenv("ANTHROPIC_API_KEY"):
        mentor = MentorAgent(graph, vector_index, schema, vault_path=vault_path)
        logger.info("Mentor agent ready")
    else:
        logger.warning("ANTHROPIC_API_KEY not set — chat and insights endpoints disabled")

    # Services
    vault_service = VaultService(vault_path, graph, vector_index, schema, rebuild_all)
    chat_service = ChatService(chat_store, mentor, graph, vector_index)

    # Store on app.config for blueprint access
    app.config["vault_path"] = vault_path
    app.config["schema"] = schema
    app.config["graph"] = graph
    app.config["vector_index"] = vector_index
    app.config["chat_store"] = chat_store
    app.config["mentor"] = mentor
    app.config["rebuild_fn"] = rebuild_all
    app.config["vault_service"] = vault_service
    app.config["chat_service"] = chat_service

    # Register blueprints
    app.register_blueprint(chat_bp)
    app.register_blueprint(graph_bp)
    app.register_blueprint(vault_bp)
    app.register_blueprint(insights_bp)

    # Error handlers
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(Exception)
    def handle_error(e):
        logger.error(f"Unhandled error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5001)
