"""Tests for chat_store.py — session persistence."""

from __future__ import annotations

import json

import pytest

from chat_store import ChatStore


class TestSessionCRUD:

    def test_create_session(self, chat_store):
        session = chat_store.create_session()
        assert "id" in session
        assert session["title"] == "New Session"
        assert "created" in session
        assert "updated" in session
        assert session["messages"] == []

    def test_get_session(self, chat_store):
        session = chat_store.create_session()
        loaded = chat_store.get_session(session["id"])
        assert loaded is not None
        assert loaded["id"] == session["id"]

    def test_get_session_not_found(self, chat_store):
        assert chat_store.get_session("00000000-0000-0000-0000-000000000000") is None

    def test_delete_session(self, chat_store):
        session = chat_store.create_session()
        assert chat_store.delete_session(session["id"]) is True
        assert chat_store.get_session(session["id"]) is None

    def test_delete_session_not_found(self, chat_store):
        assert chat_store.delete_session("00000000-0000-0000-0000-000000000000") is False

    def test_rename_session(self, chat_store):
        session = chat_store.create_session()
        assert chat_store.rename_session(session["id"], "Custom Title") is True
        loaded = chat_store.get_session(session["id"])
        assert loaded["title"] == "Custom Title"

    def test_rename_session_not_found(self, chat_store):
        assert chat_store.rename_session("00000000-0000-0000-0000-000000000000", "Title") is False


class TestListSessions:

    def test_list_sessions_sorted(self, chat_store):
        s1 = chat_store.create_session()
        s2 = chat_store.create_session()
        # Append a message to s1 to make it more recently updated
        chat_store.append_message(s1["id"], {"role": "user", "content": "hello"})

        sessions = chat_store.list_sessions()
        assert len(sessions) == 2
        # s1 was updated more recently (message appended)
        assert sessions[0]["id"] == s1["id"]

    def test_list_sessions_message_count(self, chat_store):
        session = chat_store.create_session()
        chat_store.append_message(session["id"], {"role": "user", "content": "msg1"})
        chat_store.append_message(session["id"], {"role": "assistant", "content": "reply"})

        sessions = chat_store.list_sessions()
        assert sessions[0]["message_count"] == 2


class TestMessages:

    def test_append_message(self, chat_store):
        session = chat_store.create_session()
        ok = chat_store.append_message(session["id"], {
            "role": "user",
            "content": "Hello Athena",
        })
        assert ok is True
        loaded = chat_store.get_session(session["id"])
        assert len(loaded["messages"]) == 1
        assert loaded["messages"][0]["role"] == "user"
        assert loaded["messages"][0]["content"] == "Hello Athena"

    def test_append_message_auto_titles(self, chat_store):
        """First user message auto-sets session title."""
        session = chat_store.create_session()
        chat_store.append_message(session["id"], {
            "role": "user",
            "content": "What are my goals?",
        })
        loaded = chat_store.get_session(session["id"])
        assert loaded["title"] == "What are my goals?"

    def test_append_message_truncates_long_title(self, chat_store):
        session = chat_store.create_session()
        long_msg = "A" * 100
        chat_store.append_message(session["id"], {
            "role": "user",
            "content": long_msg,
        })
        loaded = chat_store.get_session(session["id"])
        assert len(loaded["title"]) == 63  # 60 + "..."
        assert loaded["title"].endswith("...")

    def test_append_message_with_graph_updates(self, chat_store):
        session = chat_store.create_session()
        chat_store.append_message(session["id"], {
            "role": "assistant",
            "content": "I see a pattern.",
            "graph_updates": [
                {"action": "create", "node_id": "new-node", "type": "goal", "title": "New"},
            ],
        })
        loaded = chat_store.get_session(session["id"])
        assert len(loaded["messages"][0]["graph_updates"]) == 1

    def test_append_message_not_found(self, chat_store):
        ok = chat_store.append_message("00000000-0000-0000-0000-000000000000", {"role": "user", "content": "test"})
        assert ok is False

    def test_get_messages_for_api(self, chat_store):
        session = chat_store.create_session()
        chat_store.append_message(session["id"], {"role": "user", "content": "hi"})
        chat_store.append_message(session["id"], {
            "role": "assistant",
            "content": "hello",
            "graph_updates": [{"action": "create"}],
        })
        api_msgs = chat_store.get_messages_for_api(session["id"])
        assert len(api_msgs) == 2
        assert api_msgs[0] == {"role": "user", "content": "hi"}
        assert api_msgs[1] == {"role": "assistant", "content": "hello"}
        # graph_updates should NOT be in API messages
        assert "graph_updates" not in api_msgs[1]


class TestDismiss:

    def test_dismiss_update(self, chat_store):
        session = chat_store.create_session()
        ok = chat_store.dismiss_update(session["id"], "node-key-123")
        assert ok is True
        loaded = chat_store.get_session(session["id"])
        assert "node-key-123" in loaded["dismissed_updates"]

    def test_dismiss_idempotent(self, chat_store):
        session = chat_store.create_session()
        chat_store.dismiss_update(session["id"], "key")
        chat_store.dismiss_update(session["id"], "key")
        loaded = chat_store.get_session(session["id"])
        assert loaded["dismissed_updates"].count("key") == 1

    def test_dismiss_not_found(self, chat_store):
        assert chat_store.dismiss_update("00000000-0000-0000-0000-000000000000", "key") is False

    def test_undismiss_update(self, chat_store):
        session = chat_store.create_session()
        chat_store.dismiss_update(session["id"], "node-a")
        chat_store.dismiss_update(session["id"], "node-b")
        chat_store.undismiss_update(session["id"], "node-a")
        loaded = chat_store.get_session(session["id"])
        assert "node-a" not in loaded["dismissed_updates"]
        assert "node-b" in loaded["dismissed_updates"]

    def test_undismiss_not_present(self, chat_store):
        """Undismissing a node that was never dismissed is a no-op."""
        session = chat_store.create_session()
        ok = chat_store.undismiss_update(session["id"], "never-dismissed")
        assert ok is True

    def test_undismiss_not_found(self, chat_store):
        assert chat_store.undismiss_update("00000000-0000-0000-0000-000000000000", "key") is False


class TestSessionNodeIds:

    def test_get_session_node_ids(self, chat_store):
        session = chat_store.create_session()
        chat_store.append_message(session["id"], {
            "role": "assistant",
            "content": "response",
            "graph_updates": [
                {"action": "create", "node_id": "goal-a"},
                {"action": "link", "source": "goal-a", "target": "person-b"},
            ],
        })
        ids = chat_store.get_session_node_ids(session["id"])
        assert ids == {"goal-a", "person-b"}

    def test_get_session_node_ids_empty(self, chat_store):
        session = chat_store.create_session()
        assert chat_store.get_session_node_ids(session["id"]) == set()

    def test_get_session_node_ids_not_found(self, chat_store):
        assert chat_store.get_session_node_ids("00000000-0000-0000-0000-000000000000") == set()


class TestTelegramSessions:
    """Phase 3: Telegram session management methods."""

    def test_get_or_create_session_creates(self, chat_store):
        session = chat_store.get_or_create_session("tg-1936233108")
        assert session["id"] == "tg-1936233108"
        assert session["title"] == "New Session"
        assert session["messages"] == []

    def test_get_or_create_session_returns_existing(self, chat_store):
        chat_store.get_or_create_session("tg-1936233108")
        chat_store.append_message("tg-1936233108", {"role": "user", "content": "hi"})
        session = chat_store.get_or_create_session("tg-1936233108")
        assert len(session["messages"]) == 1

    def test_message_count(self, chat_store):
        session = chat_store.create_session()
        assert chat_store.message_count(session["id"]) == 0
        chat_store.append_message(session["id"], {"role": "user", "content": "1"})
        chat_store.append_message(session["id"], {"role": "assistant", "content": "2"})
        assert chat_store.message_count(session["id"]) == 2

    def test_message_count_not_found(self, chat_store):
        assert chat_store.message_count("00000000-0000-0000-0000-000000000000") == 0


class TestPendingUpdates:
    """Phase 3: Pending graph update queue for Telegram confirm/dismiss flow."""

    def test_set_and_get_pending(self, chat_store):
        session = chat_store.create_session()
        updates = [
            {"action": "create", "node_id": "goal-x", "type": "goal", "title": "Goal X"},
            {"action": "link", "source": "goal-x", "target": "person-y"},
        ]
        assert chat_store.set_pending_updates(session["id"], updates) is True
        assert chat_store.get_pending_updates(session["id"]) == updates

    def test_pop_pending_update(self, chat_store):
        session = chat_store.create_session()
        updates = [
            {"action": "create", "node_id": "a", "title": "A"},
            {"action": "create", "node_id": "b", "title": "B"},
        ]
        chat_store.set_pending_updates(session["id"], updates)
        first = chat_store.pop_pending_update(session["id"])
        assert first["node_id"] == "a"
        remaining = chat_store.get_pending_updates(session["id"])
        assert len(remaining) == 1
        assert remaining[0]["node_id"] == "b"

    def test_pop_pending_empty(self, chat_store):
        session = chat_store.create_session()
        assert chat_store.pop_pending_update(session["id"]) is None

    def test_pop_pending_not_found(self, chat_store):
        assert chat_store.pop_pending_update("00000000-0000-0000-0000-000000000000") is None

    def test_set_pending_not_found(self, chat_store):
        assert chat_store.set_pending_updates("00000000-0000-0000-0000-000000000000", []) is False

    def test_get_pending_not_found(self, chat_store):
        assert chat_store.get_pending_updates("00000000-0000-0000-0000-000000000000") == []

    def test_session_id_validation_rejects_traversal(self, chat_store):
        with pytest.raises(ValueError):
            chat_store.get_or_create_session("../../etc/passwd")

    def test_session_id_rejects_non_numeric_tg(self, chat_store):
        """tg- prefix requires decimal digits, not hex."""
        with pytest.raises(ValueError):
            chat_store.get_or_create_session("tg-abcdef")

    def test_session_id_rejects_empty_tg(self, chat_store):
        with pytest.raises(ValueError):
            chat_store.get_or_create_session("tg-")

    def test_session_id_accepts_wa_hex(self, chat_store):
        """wa- prefix accepts hex characters."""
        session = chat_store.get_or_create_session("wa-abc123def456")
        assert session["id"] == "wa-abc123def456"


class TestChallengeState:
    """Sprint 3: Challenge ladder state tracking per session."""

    def test_challenge_state_default_empty(self, chat_store):
        """New session has no challenge state."""
        session = chat_store.create_session()
        challenges = chat_store.get_active_challenges(session["id"])
        assert challenges == {}

    def test_set_and_get_challenge_state(self, chat_store):
        """Set state for a node, retrieve it."""
        session = chat_store.create_session()
        state = {
            "step": 1,
            "node_title": "Discipline",
            "node_type": "value",
            "permanence": "identity",
            "history": [{"step": 1, "action": "flagged", "timestamp": "2026-03-21T10:00:00+00:00"}],
        }
        ok = chat_store.set_challenge_state(session["id"], "discipline", state)
        assert ok is True
        retrieved = chat_store.get_challenge_state(session["id"], "discipline")
        assert retrieved is not None
        assert retrieved["step"] == 1
        assert retrieved["node_title"] == "Discipline"

    def test_advance_challenge_increments_step(self, chat_store):
        """Step goes from 1 to 2."""
        session = chat_store.create_session()
        state = {"step": 1, "node_title": "Discipline", "node_type": "value", "permanence": "identity", "history": []}
        chat_store.set_challenge_state(session["id"], "discipline", state)
        updated = chat_store.advance_challenge(session["id"], "discipline")
        assert updated is not None
        assert updated["step"] == 2

    def test_advance_challenge_caps_at_5(self, chat_store):
        """Step 5 stays at 5, does not go to 6."""
        session = chat_store.create_session()
        state = {"step": 5, "node_title": "Discipline", "node_type": "value", "permanence": "identity", "history": []}
        chat_store.set_challenge_state(session["id"], "discipline", state)
        updated = chat_store.advance_challenge(session["id"], "discipline")
        assert updated is not None
        assert updated["step"] == 5

    def test_advance_challenge_appends_history(self, chat_store):
        """History grows with each advance."""
        session = chat_store.create_session()
        state = {"step": 1, "node_title": "Discipline", "node_type": "value", "permanence": "identity", "history": [{"step": 1, "action": "flagged", "timestamp": "t"}]}
        chat_store.set_challenge_state(session["id"], "discipline", state)
        updated = chat_store.advance_challenge(session["id"], "discipline")
        assert len(updated["history"]) == 2
        assert updated["history"][-1]["step"] == 2

    def test_clear_challenge_removes_state(self, chat_store):
        """Cleared node returns None."""
        session = chat_store.create_session()
        state = {"step": 2, "node_title": "Discipline", "node_type": "value", "permanence": "identity", "history": []}
        chat_store.set_challenge_state(session["id"], "discipline", state)
        ok = chat_store.clear_challenge(session["id"], "discipline")
        assert ok is True
        assert chat_store.get_challenge_state(session["id"], "discipline") is None

    def test_get_active_challenges_returns_all(self, chat_store):
        """Multiple active challenges returned."""
        session = chat_store.create_session()
        chat_store.set_challenge_state(session["id"], "discipline", {"step": 1, "node_title": "Discipline", "node_type": "value", "permanence": "identity", "history": []})
        chat_store.set_challenge_state(session["id"], "honesty", {"step": 2, "node_title": "Honesty", "node_type": "belief", "permanence": "identity", "history": []})
        challenges = chat_store.get_active_challenges(session["id"])
        assert len(challenges) == 2
        assert "discipline" in challenges
        assert "honesty" in challenges

    def test_challenge_state_backward_compat(self, chat_store):
        """Session without challenge_state key works — returns empty dict / None."""
        session = chat_store.create_session()
        # Don't write any challenge_state; the key won't exist in JSON
        assert chat_store.get_active_challenges(session["id"]) == {}
        assert chat_store.get_challenge_state(session["id"], "anything") is None

    def test_set_challenge_not_found(self, chat_store):
        """set_challenge_state returns False for missing session."""
        ok = chat_store.set_challenge_state("00000000-0000-0000-0000-000000000000", "node", {})
        assert ok is False

    def test_advance_challenge_no_session(self, chat_store):
        """advance_challenge returns None for missing session."""
        result = chat_store.advance_challenge("00000000-0000-0000-0000-000000000000", "node")
        assert result is None

    def test_advance_challenge_no_existing_challenge(self, chat_store):
        """advance_challenge returns None if no challenge exists for node."""
        session = chat_store.create_session()
        result = chat_store.advance_challenge(session["id"], "nonexistent-node")
        assert result is None

    def test_clear_challenge_not_found(self, chat_store):
        """clear_challenge returns False for missing session."""
        ok = chat_store.clear_challenge("00000000-0000-0000-0000-000000000000", "node")
        assert ok is False
