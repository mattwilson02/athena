"""Integration tests for Flask API routes.

Uses the Flask test client. Claude API calls are mocked — no API key needed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from vault_parser import VaultParser
from vault_graph import VaultGraph
from schema_parser import parse_schema, get_edge_map
from chat_store import ChatStore
from services.vault_service import VaultService
from services.chat_service import ChatService


@pytest.fixture
def app(tmp_vault, schema, graph):
    """Create a Flask test app with all services wired up."""
    from flask import Flask
    from flask_cors import CORS
    from routes.chat_routes import chat_bp
    from routes.graph_routes import graph_bp
    from routes.vault_routes import vault_bp

    app = Flask(__name__)
    CORS(app)

    parser = VaultParser(str(tmp_vault), edge_map=get_edge_map(schema))
    vector_index = MagicMock()
    vector_index.rebuild = MagicMock()
    vector_index.search = MagicMock(return_value=[])
    vector_index.find_duplicates = MagicMock(return_value=[])

    chat_store = ChatStore(str(tmp_vault / ".chat_sessions"))

    def rebuild():
        nodes, edges = parser.parse()
        graph.build_from_parsed(nodes, edges)
        vector_index.rebuild(nodes)
        return graph.get_stats()

    mentor = MagicMock()
    mentor.chat = MagicMock(return_value={
        "response": "Test response",
        "full_response": "Test response",
        "graph_updates": [],
        "relevant_nodes": [],
    })

    vault_service = VaultService(str(tmp_vault), graph, vector_index, schema, rebuild)
    chat_service = ChatService(chat_store, mentor, graph, vector_index)

    app.config["vault_path"] = str(tmp_vault)
    app.config["schema"] = schema
    app.config["graph"] = graph
    app.config["vector_index"] = vector_index
    app.config["chat_store"] = chat_store
    app.config["mentor"] = mentor
    app.config["rebuild_fn"] = rebuild
    app.config["vault_service"] = vault_service
    app.config["chat_service"] = chat_service

    app.register_blueprint(chat_bp)
    app.register_blueprint(graph_bp)
    app.register_blueprint(vault_bp)

    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


# ── Chat Routes ──────────────────────────────────────────────────────────


class TestChatRoutes:

    def test_create_session(self, client):
        resp = client.post("/api/chat/sessions")
        assert resp.status_code == 201
        data = resp.get_json()
        assert "id" in data
        assert data["title"] == "New Session"

    def test_get_sessions(self, client):
        client.post("/api/chat/sessions")
        resp = client.get("/api/chat/sessions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["sessions"]) >= 1

    def test_get_session(self, client):
        create_resp = client.post("/api/chat/sessions")
        sid = create_resp.get_json()["id"]
        resp = client.get(f"/api/chat/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.get_json()["id"] == sid

    def test_get_session_not_found(self, client):
        resp = client.get("/api/chat/sessions/nonexistent")
        assert resp.status_code == 404

    def test_delete_session(self, client):
        create_resp = client.post("/api/chat/sessions")
        sid = create_resp.get_json()["id"]
        resp = client.delete(f"/api/chat/sessions/{sid}")
        assert resp.status_code == 200
        # Confirm it's gone
        get_resp = client.get(f"/api/chat/sessions/{sid}")
        assert get_resp.status_code == 404

    def test_rename_session(self, client):
        create_resp = client.post("/api/chat/sessions")
        sid = create_resp.get_json()["id"]
        resp = client.patch(
            f"/api/chat/sessions/{sid}",
            json={"title": "Renamed"},
        )
        assert resp.status_code == 200
        session = client.get(f"/api/chat/sessions/{sid}").get_json()
        assert session["title"] == "Renamed"

    def test_send_message(self, client):
        create_resp = client.post("/api/chat/sessions")
        sid = create_resp.get_json()["id"]
        resp = client.post("/api/chat", json={
            "session_id": sid,
            "message": "Hello Athena",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "response" in data
        assert "graph_updates" in data

    def test_send_message_missing_body(self, client):
        resp = client.post("/api/chat", json={"session_id": "abc"})
        assert resp.status_code == 400

    def test_send_message_missing_session(self, client):
        resp = client.post("/api/chat", json={"message": "test"})
        assert resp.status_code == 400


# ── Graph Routes ─────────────────────────────────────────────────────────


class TestGraphRoutes:

    def test_get_graph(self, client):
        resp = client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == 4

    def test_get_stats(self, client):
        resp = client.get("/api/graph/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_nodes"] == 4
        assert "types" in data

    def test_get_node(self, client):
        resp = client.get("/api/node/learn-piano")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["node"]["id"] == "learn-piano"
        assert "neighbors" in data

    def test_get_node_not_found(self, client):
        resp = client.get("/api/node/nonexistent")
        assert resp.status_code == 404

    def test_get_schema(self, client):
        resp = client.get("/api/schema")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "domains" in data
        assert "types" in data
        assert "edges" in data

    def test_search_title_match(self, client):
        resp = client.get("/api/search?q=Piano")
        assert resp.status_code == 200
        data = resp.get_json()
        results = data["results"]
        title_matches = [r for r in results if r.get("match_type") == "title"]
        assert len(title_matches) >= 1
        assert any(r["id"] == "learn-piano" for r in title_matches)

    def test_search_missing_query(self, client):
        resp = client.get("/api/search?q=")
        assert resp.status_code == 400

    def test_get_activity(self, client):
        resp = client.get("/api/activity")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "activities" in data
        # Our test nodes have created dates
        assert len(data["activities"]) >= 1

    def test_get_nodes_by_type(self, client):
        resp = client.get("/api/nodes?type=goal")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["nodes"]) == 2


# ── Vault Routes ─────────────────────────────────────────────────────────


class TestVaultRoutes:

    def test_vault_write(self, client, tmp_vault):
        resp = client.post("/api/vault/write", json={
            "node_id": "test-write",
            "title": "Test Write",
            "type": "goal",
            "content": "From test.",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True
        assert (tmp_vault / "Self" / "Goals" / "test-write.md").exists()

    def test_vault_write_invalid_type(self, client):
        resp = client.post("/api/vault/write", json={
            "node_id": "bad",
            "title": "Bad",
            "type": "unicorn",
        })
        assert resp.status_code == 400

    def test_vault_update(self, client):
        resp = client.post("/api/vault/update", json={
            "node_id": "learn-piano",
            "changes": {"title": "Master Piano"},
        })
        assert resp.status_code == 200
        assert resp.get_json().get("ok") is True

    def test_vault_rebuild(self, client):
        resp = client.post("/api/vault/rebuild")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True
        assert "stats" in data

    def test_vault_write_no_body(self, client):
        resp = client.post("/api/vault/write", content_type="application/json")
        assert resp.status_code == 400
