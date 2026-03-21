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
    chat_service = ChatService(chat_store, mentor, graph, vector_index, vault_service=vault_service)

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

    def test_get_sessions_excludes_telegram_by_default(self, client):
        """Desktop UI should not see tg-* sessions."""
        # Create a desktop session
        client.post("/api/chat/sessions")
        # Create a Telegram session via simple chat
        client.post("/api/chat/simple", json={
            "session_id": "tg-1234567890",
            "message": "Hello",
        })
        resp = client.get("/api/chat/sessions")
        data = resp.get_json()
        tg_sessions = [s for s in data["sessions"] if s["id"].startswith("tg-")]
        assert len(tg_sessions) == 0

    def test_get_sessions_source_all(self, client):
        """source=all returns both desktop and Telegram sessions."""
        client.post("/api/chat/sessions")
        client.post("/api/chat/simple", json={
            "session_id": "tg-1234567890",
            "message": "Hello",
        })
        resp = client.get("/api/chat/sessions?source=all")
        data = resp.get_json()
        assert len(data["sessions"]) >= 2

    def test_get_session(self, client):
        create_resp = client.post("/api/chat/sessions")
        sid = create_resp.get_json()["id"]
        resp = client.get(f"/api/chat/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.get_json()["id"] == sid

    def test_get_session_not_found(self, client):
        resp = client.get("/api/chat/sessions/00000000-0000-0000-0000-000000000000")
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


# ── Simple Chat Routes (Phase 3) ────────────────────────────────────────


class TestSimpleChatRoutes:
    """POST /api/chat/simple — non-streaming chat for Telegram/n8n."""

    def test_simple_chat(self, client):
        resp = client.post("/api/chat/simple", json={
            "session_id": "tg-1936233108",
            "message": "Hello Athena",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "response" in data

    def test_simple_chat_auto_creates_session(self, client):
        """tg-* sessions are auto-created by send_simple_message."""
        client.post("/api/chat/simple", json={
            "session_id": "tg-5551234567",
            "message": "First message",
        })
        resp = client.get("/api/chat/sessions/tg-5551234567")
        assert resp.status_code == 200

    def test_simple_chat_missing_message(self, client):
        resp = client.post("/api/chat/simple", json={
            "session_id": "tg-1936233108",
        })
        assert resp.status_code == 400

    def test_simple_chat_missing_session_id(self, client):
        resp = client.post("/api/chat/simple", json={
            "message": "Hello",
        })
        assert resp.status_code == 400

    def test_simple_chat_confirm_no_pending(self, client):
        """Confirm keyword with nothing pending returns info message."""
        # Create session first
        client.post("/api/chat/simple", json={
            "session_id": "tg-1112223334",
            "message": "Hello",
        })
        resp = client.post("/api/chat/simple", json={
            "session_id": "tg-1112223334",
            "message": "yes",
        })
        assert resp.status_code == 200
        assert "Nothing pending" in resp.get_json()["response"]

    def test_simple_chat_dismiss_no_pending(self, client):
        client.post("/api/chat/simple", json={
            "session_id": "tg-4445556667",
            "message": "Hello",
        })
        resp = client.post("/api/chat/simple", json={
            "session_id": "tg-4445556667",
            "message": "no",
        })
        assert resp.status_code == 200
        assert "Nothing pending" in resp.get_json()["response"]

    def test_simple_chat_with_graph_updates_queues_pending(self, app, client):
        """When mentor returns graph_updates, they become pending with a confirm prompt."""
        mentor = app.config["mentor"]
        mentor.chat.return_value = {
            "response": "I see you want to learn guitar.",
            "full_response": "I see you want to learn guitar.",
            "graph_updates": [
                {"action": "create", "node_id": "learn-guitar", "type": "goal", "title": "Learn Guitar"},
            ],
            "relevant_nodes": [],
        }
        resp = client.post("/api/chat/simple", json={
            "session_id": "tg-7778889990",
            "message": "I want to learn guitar",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "YES" in data["response"]
        assert "Learn Guitar" in data["response"]

    def test_simple_chat_confirm_writes_to_vault(self, app, client, tmp_vault):
        """Confirming a pending create writes the node to vault."""
        mentor = app.config["mentor"]
        mentor.chat.return_value = {
            "response": "Adding a new goal.",
            "full_response": "Adding a new goal.",
            "graph_updates": [
                {"action": "create", "node_id": "test-confirm", "type": "goal", "title": "Test Confirm"},
            ],
            "relevant_nodes": [],
        }
        # Send message that triggers a graph update
        client.post("/api/chat/simple", json={
            "session_id": "tg-8889990001",
            "message": "Create a test goal",
        })
        # Confirm it
        resp = client.post("/api/chat/simple", json={
            "session_id": "tg-8889990001",
            "message": "yes",
        })
        assert resp.status_code == 200
        assert "Saved" in resp.get_json()["response"]
        assert (tmp_vault / "Self" / "Goals" / "test-confirm.md").exists()


class TestChatIdAllowlist:
    """Telegram chat ID allowlist — reject unknown users."""

    def test_allowed_chat_id_passes(self, app, client):
        app.config["athena_config"] = {
            "telegram": {"allowed_chat_ids": [1936233108]},
        }
        resp = client.post("/api/chat/simple", json={
            "session_id": "tg-1936233108",
            "message": "Hello",
        })
        assert resp.status_code == 200

    def test_blocked_chat_id_returns_403(self, app, client):
        app.config["athena_config"] = {
            "telegram": {"allowed_chat_ids": [1936233108]},
        }
        resp = client.post("/api/chat/simple", json={
            "session_id": "tg-9999999999",
            "message": "Hello",
        })
        assert resp.status_code == 403

    def test_no_allowlist_allows_all(self, app, client):
        """If allowed_chat_ids is empty or missing, all chat IDs pass."""
        app.config["athena_config"] = {"telegram": {}}
        resp = client.post("/api/chat/simple", json={
            "session_id": "tg-9999999999",
            "message": "Hello",
        })
        assert resp.status_code == 200


class TestSimpleChatMaxMessages:
    """Max message cap on Telegram sessions."""

    def test_max_messages_returns_429(self, app, client):
        app.config["athena_config"] = {
            "telegram": {"max_session_messages": 2},
        }
        # Send 2 messages to fill the cap
        for _ in range(2):
            client.post("/api/chat/simple", json={
                "session_id": "tg-1001001001",
                "message": "fill",
            })
        # Third should be rejected
        resp = client.post("/api/chat/simple", json={
            "session_id": "tg-1001001001",
            "message": "too many",
        })
        assert resp.status_code == 429


class TestStreamingEndpoint:
    """Basic tests for POST /api/chat/stream."""

    def test_stream_missing_message(self, client):
        resp = client.post("/api/chat/stream", json={"session_id": "abc"})
        assert resp.status_code == 400

    def test_stream_missing_session_id(self, client):
        resp = client.post("/api/chat/stream", json={"message": "test"})
        assert resp.status_code == 400


# ── Accountability Route ──────────────────────────────────────────────────


class TestAccountabilityRoute:

    def test_accountability_endpoint_returns_structure(self, client):
        """GET /api/accountability returns streaks, overdue, and summary keys."""
        resp = client.get("/api/accountability")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "streaks" in data
        assert "overdue" in data
        assert "summary" in data

    def test_accountability_endpoint_returns_streaks(self, client):
        """Endpoint returns a list for streaks (may be empty with test graph)."""
        resp = client.get("/api/accountability")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data["streaks"], list)

    def test_accountability_endpoint_returns_overdue(self, client):
        """Endpoint returns a list for overdue (may be empty with test graph)."""
        resp = client.get("/api/accountability")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data["overdue"], list)

    def test_accountability_endpoint_summary_counts(self, client):
        """summary.on_track + at_risk + broken == total_habits."""
        resp = client.get("/api/accountability")
        assert resp.status_code == 200
        s = resp.get_json()["summary"]
        assert s["on_track"] + s["at_risk"] + s["broken"] == s["total_habits"]

    def test_accountability_endpoint_empty_graph(self, client):
        """Empty graph returns zero counts and empty lists."""
        resp = client.get("/api/accountability")
        assert resp.status_code == 200
        data = resp.get_json()
        summary = data["summary"]
        # Test graph has no habit nodes, so habits = 0
        # overdue depends on test graph nodes/dates
        assert isinstance(summary["total_habits"], int)
        assert isinstance(summary["overdue_count"], int)

    def test_accountability_endpoint_error_handling(self, client, app):
        """Accountability service error → 500 response."""
        from unittest.mock import patch
        with patch(
            "routes.graph_routes.calculate_streaks",
            side_effect=RuntimeError("boom"),
        ):
            resp = client.get("/api/accountability")
            assert resp.status_code == 500
            data = resp.get_json()
            assert "error" in data


# ── Commitment Metadata Post-Processing ──────────────────────────────────


class TestCommitmentMetadataEnrichment:
    """Test ChatService._enrich_commitment_metadata()."""

    def _make_service(self):
        """Build a minimal ChatService for unit-testing post-processing."""
        from unittest.mock import MagicMock
        from services.chat_service import ChatService
        graph = MagicMock()
        graph.get_node = MagicMock(return_value=None)
        vi = MagicMock()
        vi.find_duplicates = MagicMock(return_value=[])
        svc = ChatService.__new__(ChatService)
        svc.chat_store = MagicMock()
        svc.mentor = MagicMock()
        svc.graph = graph
        svc.vector_index = vi
        svc.vault_service = MagicMock()
        return svc

    def test_enrich_commitment_valid_date(self):
        """Update with valid committed_on passes through unchanged."""
        svc = self._make_service()
        updates = [{
            "action": "create",
            "node_id": "my-task",
            "type": "task",
            "frontmatter": {
                "committed_on": "2026-03-21",
                "commitment_context": "Promised by Friday",
            },
        }]
        result = svc._enrich_commitment_metadata(updates)
        assert result[0]["frontmatter"]["committed_on"] == "2026-03-21"
        assert result[0]["frontmatter"]["commitment_context"] == "Promised by Friday"

    def test_enrich_commitment_invalid_date_stripped(self):
        """Update with malformed committed_on has it removed."""
        svc = self._make_service()
        updates = [{
            "action": "create",
            "node_id": "my-task",
            "type": "task",
            "frontmatter": {
                "committed_on": "not-a-date",
                "commitment_context": "Some context",
            },
        }]
        result = svc._enrich_commitment_metadata(updates)
        assert "committed_on" not in result[0]["frontmatter"]

    def test_enrich_commitment_context_default(self):
        """Update with committed_on but no commitment_context gets empty string default."""
        svc = self._make_service()
        updates = [{
            "action": "create",
            "node_id": "my-task",
            "type": "task",
            "frontmatter": {"committed_on": "2026-03-21"},
        }]
        result = svc._enrich_commitment_metadata(updates)
        assert result[0]["frontmatter"]["commitment_context"] == ""

    def test_enrich_no_commitment_fields_unchanged(self):
        """Update without commitment fields is returned unchanged."""
        svc = self._make_service()
        updates = [{
            "action": "create",
            "node_id": "my-task",
            "type": "task",
            "frontmatter": {"status": "active", "priority": "high"},
        }]
        result = svc._enrich_commitment_metadata(updates)
        assert result[0]["frontmatter"] == {"status": "active", "priority": "high"}

    def test_enrich_update_action_with_commitment(self):
        """Update action with committed_on in changes.frontmatter is also processed."""
        svc = self._make_service()
        updates = [{
            "action": "update",
            "node_id": "existing-task",
            "changes": {
                "frontmatter": {"committed_on": "2026-03-21"},
            },
        }]
        result = svc._enrich_commitment_metadata(updates)
        assert result[0]["changes"]["frontmatter"]["commitment_context"] == ""

    def test_enrich_link_action_unchanged(self):
        """Link actions are not modified."""
        svc = self._make_service()
        updates = [{"action": "link", "source": "a", "target": "b", "type": "relates_to"}]
        result = svc._enrich_commitment_metadata(updates)
        assert result == [{"action": "link", "source": "a", "target": "b", "type": "relates_to"}]


class TestAccountabilityErrorHandling:
    """Test that accountability service errors don't break chat flow."""

    def test_accountability_error_doesnt_break_chat(self, client):
        """Mock accountability to raise; chat still works with empty alerts."""
        from unittest.mock import patch

        create_resp = client.post("/api/chat/sessions")
        sid = create_resp.get_json()["id"]

        with patch(
            "services.chat_service.ChatService._build_alerts",
            side_effect=RuntimeError("accountability exploded"),
        ):
            resp = client.post("/api/chat", json={
                "session_id": sid,
                "message": "Hello Athena",
            })
        # Chat should still succeed even if accountability fails
        assert resp.status_code == 200
        data = resp.get_json()
        assert "response" in data


# ── State Inference Error Handling ────────────────────────────────────────


class TestStateInferenceErrorHandling:

    def test_state_inference_error_doesnt_break_chat(self, client):
        """Mock state_service.infer_state to raise → chat proceeds with empty state."""
        from unittest.mock import patch

        create_resp = client.post("/api/chat/sessions")
        sid = create_resp.get_json()["id"]

        with patch(
            "services.state_service.infer_state",
            side_effect=RuntimeError("state inference exploded"),
        ):
            resp = client.post("/api/chat", json={
                "session_id": sid,
                "message": "Hello Athena",
            })
        # Chat should still succeed even if state inference fails
        assert resp.status_code == 200
        data = resp.get_json()
        assert "response" in data


# ── Fundamentals in Accountability Endpoint ───────────────────────────────


class TestAccountabilityFundamentals:

    def test_accountability_endpoint_returns_fundamentals(self, client):
        """GET /api/accountability response includes 'fundamentals' key."""
        resp = client.get("/api/accountability")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "fundamentals" in data
        assert "fundamentals_summary" in data

    def test_accountability_fundamentals_is_list(self, client):
        """fundamentals is a list."""
        resp = client.get("/api/accountability")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data["fundamentals"], list)

    def test_accountability_fundamentals_summary_counts(self, client):
        """fundamentals_summary totals match the array."""
        resp = client.get("/api/accountability")
        assert resp.status_code == 200
        data = resp.get_json()
        fundamentals = data["fundamentals"]
        summary = data["fundamentals_summary"]
        assert summary["total"] == len(fundamentals)
        assert summary["active"] + summary["neglected"] + summary["no_data"] == summary["total"]

    def test_accountability_fundamentals_all_statuses(self, client):
        """fundamentals entries have valid status values."""
        resp = client.get("/api/accountability")
        assert resp.status_code == 200
        data = resp.get_json()
        valid_statuses = {"active", "neglected", "no_data"}
        for f in data["fundamentals"]:
            assert f["status"] in valid_statuses

    def test_accountability_fundamentals_error_handling(self, client, app):
        """check_fundamentals raises → 500 response."""
        from unittest.mock import patch
        with patch(
            "routes.graph_routes.check_fundamentals",
            side_effect=RuntimeError("fundamentals boom"),
        ):
            resp = client.get("/api/accountability")
            assert resp.status_code == 500
            data = resp.get_json()
            assert "error" in data
