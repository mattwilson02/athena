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

    def test_delete_route_returns_ok(self, client, tmp_vault):
        resp = client.delete("/api/vault/node/learn-piano")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True
        assert data["node_id"] == "learn-piano"
        assert "archived_to" in data
        assert "stats" in data

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/vault/node/no-such-node")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data


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


# ── Debug Retrieval Route ────────────────────────────────────────────────


class TestDebugRetrievalRoute:

    def test_debug_retrieval_endpoint_exists(self, client, app):
        """GET /api/debug/retrieval?q=test → 200 response with diagnostics."""
        mentor = app.config["mentor"]
        mentor.get_context_debug = MagicMock(return_value={
            "query": "test",
            "intent": {"intent": "general", "k": 5, "compact": False, "pre_filter": None, "scoring_adjustments": {}},
            "date_range": None,
            "domains": [],
            "candidates": [],
            "context_length_chars": 100,
            "context_length_tokens_est": 25,
            "nodes_in_context": {"tier1": 0, "tier2": 0, "tier3": 0},
        })
        resp = client.get("/api/debug/retrieval?q=test")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "query" in data
        assert "intent" in data
        assert "candidates" in data

    def test_debug_retrieval_endpoint_no_query(self, client):
        """GET /api/debug/retrieval (no q param) → 400 response."""
        resp = client.get("/api/debug/retrieval")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data


# ── Relationship Routes ───────────────────────────────────────────────────


class TestRelationshipRoutes:
    """Tests for GET /api/relationships and extended /api/accountability."""

    def test_accountability_includes_relationships_summary(self, client):
        """GET /api/accountability response includes 'relationships_summary' key."""
        resp = client.get("/api/accountability")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "relationships_summary" in data

    def test_accountability_relationships_summary_structure(self, client):
        """relationships_summary has required keys."""
        resp = client.get("/api/accountability")
        assert resp.status_code == 200
        rs = resp.get_json()["relationships_summary"]
        assert "total_persons" in rs
        assert "active" in rs
        assert "drifting" in rs
        assert "neglected" in rs
        assert "no_data" in rs
        assert "most_mentioned" in rs

    def test_relationships_endpoint_returns_data(self, client):
        """GET /api/relationships → 200 with relationships array."""
        resp = client.get("/api/relationships")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "relationships" in data
        assert "summary" in data
        assert isinstance(data["relationships"], list)

    def test_relationships_endpoint_empty_graph(self, client):
        """No person nodes → empty array, zero counts."""
        # Test graph has one person node (alice), so we patch to empty
        from unittest.mock import patch
        with patch(
            "routes.graph_routes.scan_mentions",
            return_value=[],
        ):
            with patch(
                "routes.graph_routes.assess_relationship_health",
                return_value=[],
            ):
                resp = client.get("/api/relationships")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["relationships"] == []
        summary = data["summary"]
        assert summary["total_persons"] == 0
        assert summary["active"] == 0
        assert summary["drifting"] == 0
        assert summary["neglected"] == 0

    def test_relationships_endpoint_summary_counts_match(self, client):
        """summary counts match the relationships array."""
        resp = client.get("/api/relationships")
        assert resp.status_code == 200
        data = resp.get_json()
        rels = data["relationships"]
        summary = data["summary"]
        assert summary["total_persons"] == len(rels)
        computed_active = sum(1 for r in rels if r["health"] == "active")
        computed_drifting = sum(1 for r in rels if r["health"] == "drifting")
        computed_neglected = sum(1 for r in rels if r["health"] == "neglected")
        computed_no_data = sum(1 for r in rels if r["health"] == "no_data")
        assert summary["active"] == computed_active
        assert summary["drifting"] == computed_drifting
        assert summary["neglected"] == computed_neglected
        assert summary["no_data"] == computed_no_data

    def test_relationships_endpoint_sorted_by_health(self, client):
        """Neglected persons appear before active in sorted results."""
        from unittest.mock import patch
        from datetime import date

        rels = [
            {
                "person_id": "alice", "person_title": "Alice", "relationship": "friend",
                "expected_frequency": "weekly", "mention_count": 3,
                "last_mentioned": "2026-01-01", "days_since_mention": 80,
                "health": "neglected", "drift_days": 52, "influence_score": 0.3,
                "influence_rank": 2, "context_profile": "neutral",
                "mention_contexts": {"positive": 0, "negative": 0, "planning": 0, "neutral": 3},
                "recent_topics": [],
            },
            {
                "person_id": "bob", "person_title": "Bob", "relationship": "friend",
                "expected_frequency": "weekly", "mention_count": 10,
                "last_mentioned": "2026-03-22", "days_since_mention": 1,
                "health": "active", "drift_days": 0, "influence_score": 1.0,
                "influence_rank": 1, "context_profile": "mostly_positive",
                "mention_contexts": {"positive": 8, "negative": 1, "planning": 1, "neutral": 0},
                "recent_topics": [],
            },
        ]

        with patch("routes.graph_routes.scan_mentions", return_value=[]):
            with patch("routes.graph_routes.assess_relationship_health", return_value=rels):
                resp = client.get("/api/relationships")
        assert resp.status_code == 200
        data = resp.get_json()
        result_rels = data["relationships"]
        # neglected should come first
        assert result_rels[0]["health"] == "neglected"
        assert result_rels[1]["health"] == "active"

    def test_relationships_endpoint_error_handling(self, client):
        """scan_mentions raises → 500 response."""
        from unittest.mock import patch
        with patch(
            "routes.graph_routes.scan_mentions",
            side_effect=RuntimeError("service exploded"),
        ):
            resp = client.get("/api/relationships")
        assert resp.status_code == 500
        data = resp.get_json()
        assert "error" in data

    def test_relationship_error_doesnt_break_chat(self, client):
        """Mock relationship service to raise → chat proceeds normally."""
        from unittest.mock import patch

        create_resp = client.post("/api/chat/sessions")
        sid = create_resp.get_json()["id"]

        with patch(
            "services.relationship_service.scan_mentions",
            side_effect=RuntimeError("relationship service exploded"),
        ):
            resp = client.post("/api/chat", json={
                "session_id": sid,
                "message": "Hello Athena",
            })
        assert resp.status_code == 200
        data = resp.get_json()
        assert "response" in data


class TestRelationshipPersistenceThrottle:
    """Test that relationship persistence is throttled."""

    def test_relationship_persist_throttled(self, client):
        """Two messages within 1 hour → persistence runs at most once."""
        from unittest.mock import patch
        from services.chat_service import ChatService

        # Reset the throttle timestamp
        ChatService._last_relationship_persist = 0.0

        create_resp = client.post("/api/chat/sessions")
        sid = create_resp.get_json()["id"]

        persist_calls = []

        def mock_persist(vault_service, relationships, today):
            persist_calls.append(1)
            return len(relationships)

        with patch(
            "services.relationship_service.persist_mention_stats",
            side_effect=mock_persist,
        ):
            with patch(
                "services.relationship_service.scan_mentions",
                return_value=[],
            ):
                with patch(
                    "services.relationship_service.assess_relationship_health",
                    return_value=[],
                ):
                    # First message
                    client.post("/api/chat", json={"session_id": sid, "message": "msg 1"})
                    # Second message (same hour)
                    client.post("/api/chat", json={"session_id": sid, "message": "msg 2"})

        # Persistence should have run at most once
        assert len(persist_calls) <= 1


# ── Kind-Aware Accountability Tests ──────────────────────────────────────


class TestAccountabilityKindAware:
    """Test kind-aware fields in GET /api/accountability response."""

    def test_accountability_endpoint_includes_kind(self, client):
        """Streak entries returned by the endpoint include a 'kind' field."""
        from unittest.mock import patch

        fake_streaks = [
            {
                "habit_id": "strength-training",
                "habit_title": "Strength Training",
                "frequency": "3x/week",
                "status": "active",
                "kind": "build",
                "current_streak": 2,
                "last_completed": "2026-03-22",
                "days_since_last": 2,
                "days_clean": None,
                "last_occurrence": None,
                "next_due": None,
                "days_until_due": None,
                "streak_status": "on_track",
            }
        ]
        with patch("routes.graph_routes.calculate_streaks", return_value=fake_streaks):
            resp = client.get("/api/accountability")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["streaks"]) == 1
        assert "kind" in data["streaks"][0]
        assert data["streaks"][0]["kind"] == "build"

    def test_accountability_summary_by_kind(self, client):
        """Summary includes a by_kind breakdown."""
        resp = client.get("/api/accountability")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "by_kind" in data["summary"]
        by_kind = data["summary"]["by_kind"]
        assert "build" in by_kind
        assert "break" in by_kind
        assert "periodic" in by_kind

    def test_accountability_break_habit_days_clean(self, client):
        """Break habit entry has days_clean field in the response."""
        from unittest.mock import patch

        fake_streaks = [
            {
                "habit_id": "nicotine",
                "habit_title": "Nicotine Pouches",
                "frequency": "daily",
                "status": "quitting",
                "kind": "break",
                "current_streak": None,
                "last_completed": None,
                "days_since_last": None,
                "days_clean": 12,
                "last_occurrence": "2026-03-12",
                "next_due": None,
                "days_until_due": None,
                "streak_status": "on_track",
            }
        ]
        with patch("routes.graph_routes.calculate_streaks", return_value=fake_streaks):
            resp = client.get("/api/accountability")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["streaks"]) == 1
        streak = data["streaks"][0]
        assert "days_clean" in streak
        assert streak["days_clean"] == 12
