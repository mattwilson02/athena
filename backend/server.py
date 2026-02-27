"""Flask API entry point for Athena — thin app factory."""

from __future__ import annotations

import logging
import os

import yaml
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
from middleware.auth import require_auth
from middleware.audit import log_audit

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _load_config() -> dict:
    """Load config from YAML file, falling back to defaults."""
    config_path = os.getenv("CONFIG_PATH", "/config/config.yaml")
    if os.path.isfile(config_path):
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
        logger.info(f"Loaded config from {config_path}")
        return config
    logger.info("No config file found — using defaults")
    return {}


def _load_secrets(config: dict) -> dict:
    """Load secrets from files into config. Falls back to env vars."""
    secrets = {}

    # API key: file first, then env var
    key_path = "/secrets/anthropic_api_key.txt"
    if os.path.isfile(key_path):
        with open(key_path, "r") as f:
            secrets["anthropic_api_key"] = f.read().strip()
        logger.info("Loaded API key from secrets file")
    else:
        secrets["anthropic_api_key"] = os.getenv("ANTHROPIC_API_KEY", "")
        if secrets["anthropic_api_key"]:
            logger.info("Loaded API key from env var (dev mode)")

    # Export so the Anthropic SDK finds it (SDK reads ANTHROPIC_API_KEY env var)
    if secrets.get("anthropic_api_key"):
        os.environ["ANTHROPIC_API_KEY"] = secrets["anthropic_api_key"]

    config["_secrets"] = secrets
    return config


def _load_auth_tokens() -> dict:
    """Load bearer tokens from secrets file. Returns {token: username} map."""
    tokens_path = "/secrets/auth_tokens.yaml"
    if os.path.isfile(tokens_path):
        with open(tokens_path, "r") as f:
            data = yaml.safe_load(f) or {}
        tokens = data.get("tokens", {})
        logger.info(f"Loaded {len(tokens)} auth token(s)")
        return tokens
    logger.info("No auth tokens file — auth disabled")
    return {}


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app, origins=["http://localhost:5173", "http://localhost:8080"])

    # Load config and secrets
    config = _load_config()
    config = _load_secrets(config)
    auth_tokens = _load_auth_tokens()

    app.config["athena_config"] = config
    app.config["auth_tokens"] = auth_tokens

    # Register middleware
    app.before_request(require_auth)
    app.after_request(log_audit)

    # Resolve vault path
    vault_path = os.getenv("VAULT_PATH", "../vault")
    if not os.path.isabs(vault_path):
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
    api_key = config.get("_secrets", {}).get("anthropic_api_key", "")
    if api_key:
        mentor = MentorAgent(graph, vector_index, schema, vault_path=vault_path)
        logger.info("Mentor agent ready")
    else:
        logger.warning("No API key — chat and insights endpoints disabled")

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

    # Health endpoint
    @app.route("/api/health")
    def health():
        return jsonify({"status": "ok"})

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
    is_production = os.getenv("FLASK_ENV") == "production"
    app.run(
        host="0.0.0.0" if is_production else "127.0.0.1",
        port=5001,
        debug=not is_production,
    )
