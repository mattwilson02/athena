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
