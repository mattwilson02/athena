"""Tests for Sprint 3 challenge ladder detection and flow."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chat_store import ChatStore
from services.chat_service import ChatService
from mentor_agent import MentorAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chat_service(tmp_path, graph_nodes: dict | None = None):
    """Create a ChatService with real ChatStore and mock mentor/graph/vector."""
    chat_store = ChatStore(str(tmp_path / "sessions"))

    graph = MagicMock()
    graph.get_node = MagicMock(side_effect=lambda nid: (graph_nodes or {}).get(nid))

    vector_index = MagicMock()
    vector_index.find_duplicates = MagicMock(return_value=[])

    mentor = MagicMock()

    service = ChatService(chat_store, mentor, graph, vector_index)
    return service, chat_store


def _make_session(chat_store: ChatStore) -> str:
    return chat_store.create_session()["id"]


# ---------------------------------------------------------------------------
# Challenge ladder detection — _check_challenge_triggers
# ---------------------------------------------------------------------------

class TestIdentityUpdateTriggersChallenge:

    def test_identity_update_triggers_challenge(self, tmp_path):
        """Updating a value node status to 'abandoned' creates challenge at step 1."""
        nodes = {
            "discipline": {"id": "discipline", "type": "value", "title": "Discipline"},
        }
        service, store = _make_chat_service(tmp_path, nodes)
        session_id = _make_session(store)

        updates = [
            {"action": "update", "node_id": "discipline", "changes": {"frontmatter": {"status": "abandoned"}}},
        ]
        result = service._check_challenge_triggers(session_id, updates)

        # Update held back (step 1)
        assert len(result) == 0
        state = store.get_challenge_state(session_id, "discipline")
        assert state is not None
        assert state["step"] == 1

    def test_strategic_update_no_challenge(self, tmp_path):
        """Updating a goal node does NOT trigger the challenge ladder."""
        nodes = {
            "learn-piano": {"id": "learn-piano", "type": "goal", "title": "Learn Piano"},
        }
        service, store = _make_chat_service(tmp_path, nodes)
        session_id = _make_session(store)

        updates = [
            {"action": "update", "node_id": "learn-piano", "changes": {"frontmatter": {"status": "abandoned"}}},
        ]
        result = service._check_challenge_triggers(session_id, updates)

        # Update passes through — no challenge for strategic nodes
        assert len(result) == 1
        assert store.get_challenge_state(session_id, "learn-piano") is None

    def test_challenge_advances_on_repeat(self, tmp_path):
        """Second message about same node goes to step 2."""
        nodes = {
            "discipline": {"id": "discipline", "type": "value", "title": "Discipline"},
        }
        service, store = _make_chat_service(tmp_path, nodes)
        session_id = _make_session(store)

        updates = [
            {"action": "update", "node_id": "discipline", "changes": {"frontmatter": {"status": "abandoned"}}},
        ]

        # First call — step 1
        service._check_challenge_triggers(session_id, updates)
        assert store.get_challenge_state(session_id, "discipline")["step"] == 1

        # Second call — step 2
        service._check_challenge_triggers(session_id, updates)
        assert store.get_challenge_state(session_id, "discipline")["step"] == 2

    def test_challenge_step_5_releases_update(self, tmp_path):
        """At step 5, graph update is released to the frontend."""
        nodes = {
            "discipline": {"id": "discipline", "type": "value", "title": "Discipline"},
        }
        service, store = _make_chat_service(tmp_path, nodes)
        session_id = _make_session(store)

        # Manually set to step 4 so next call advances to 5 and releases.
        store.set_challenge_state(session_id, "discipline", {
            "step": 4,
            "node_title": "Discipline",
            "node_type": "value",
            "permanence": "identity",
            "history": [],
        })

        updates = [
            {"action": "update", "node_id": "discipline", "changes": {"frontmatter": {"status": "abandoned"}}},
        ]
        result = service._check_challenge_triggers(session_id, updates)

        # Released at step 5
        assert len(result) == 1
        assert result[0]["_challenge"]["step"] == 5
        # Challenge cleared after release
        assert store.get_challenge_state(session_id, "discipline") is None

    def test_create_identity_no_challenge(self, tmp_path):
        """Creating a new value node does NOT trigger the challenge ladder."""
        service, store = _make_chat_service(tmp_path)
        session_id = _make_session(store)

        updates = [
            {"action": "create", "node_id": "new-value", "type": "value", "title": "New Value", "content": "A new core value."},
        ]
        result = service._check_challenge_triggers(session_id, updates)

        assert len(result) == 1
        assert store.get_challenge_state(session_id, "new-value") is None

    def test_content_change_triggers_challenge(self, tmp_path):
        """Changing a value's content triggers the challenge (not just status)."""
        nodes = {
            "discipline": {"id": "discipline", "type": "value", "title": "Discipline"},
        }
        service, store = _make_chat_service(tmp_path, nodes)
        session_id = _make_session(store)

        updates = [
            {"action": "update", "node_id": "discipline", "changes": {"content": "Revised definition of discipline."}},
        ]
        result = service._check_challenge_triggers(session_id, updates)

        assert len(result) == 0
        assert store.get_challenge_state(session_id, "discipline") is not None

    def test_multiple_challenges_tracked(self, tmp_path):
        """Two identity nodes can be challenged simultaneously."""
        nodes = {
            "discipline": {"id": "discipline", "type": "value", "title": "Discipline"},
            "honesty": {"id": "honesty", "type": "belief", "title": "Honesty"},
        }
        service, store = _make_chat_service(tmp_path, nodes)
        session_id = _make_session(store)

        updates = [
            {"action": "update", "node_id": "discipline", "changes": {"frontmatter": {"status": "abandoned"}}},
            {"action": "update", "node_id": "honesty", "changes": {"frontmatter": {"status": "abandoned"}}},
        ]
        result = service._check_challenge_triggers(session_id, updates)

        assert len(result) == 0
        assert store.get_challenge_state(session_id, "discipline") is not None
        assert store.get_challenge_state(session_id, "honesty") is not None

    def test_challenge_cleared_after_release(self, tmp_path):
        """Challenge state is removed after step 5 acceptance."""
        nodes = {
            "discipline": {"id": "discipline", "type": "value", "title": "Discipline"},
        }
        service, store = _make_chat_service(tmp_path, nodes)
        session_id = _make_session(store)

        store.set_challenge_state(session_id, "discipline", {
            "step": 5,
            "node_title": "Discipline",
            "node_type": "value",
            "permanence": "identity",
            "history": [],
        })

        # Manually advance already at 5 — should still release and clear
        updates = [
            {"action": "update", "node_id": "discipline", "changes": {"frontmatter": {"status": "abandoned"}}},
        ]
        result = service._check_challenge_triggers(session_id, updates)

        assert len(result) == 1
        assert store.get_challenge_state(session_id, "discipline") is None

    def test_update_type_from_update_field(self, tmp_path):
        """Node type can come from the update's own 'type' field."""
        # graph.get_node returns None so type must come from update itself
        service, store = _make_chat_service(tmp_path, {})
        session_id = _make_session(store)

        updates = [
            {"action": "update", "node_id": "new-fear", "type": "fear", "changes": {"frontmatter": {"status": "cancelled"}}},
        ]
        result = service._check_challenge_triggers(session_id, updates)

        assert len(result) == 0
        assert store.get_challenge_state(session_id, "new-fear") is not None


# ---------------------------------------------------------------------------
# Challenge note format — _build_challenge_note
# ---------------------------------------------------------------------------

class TestChallengeNoteFormat:

    def test_challenge_note_empty_when_no_challenges(self):
        """Empty challenges produce empty string."""
        from mentor_agent import MentorAgent
        note = MentorAgent._build_challenge_note({})
        assert note == ""

    def test_challenge_note_step_1_instruction(self):
        """Step 1 note contains 'Flag the gap' instruction."""
        challenges = {
            "discipline": {
                "step": 1,
                "node_title": "Discipline",
                "node_type": "value",
                "permanence": "identity",
                "history": [],
            }
        }
        from mentor_agent import MentorAgent
        note = MentorAgent._build_challenge_note(challenges)
        assert "CHALLENGE LADDER" in note
        assert "Discipline" in note
        assert "Flag the gap" in note
        assert "step 1" in note.lower() or "1 of 5" in note

    def test_challenge_note_step_5_instruction(self):
        """Step 5 note contains 'Accept with full context'."""
        challenges = {
            "discipline": {
                "step": 5,
                "node_title": "Discipline",
                "node_type": "value",
                "permanence": "identity",
                "history": [],
            }
        }
        from mentor_agent import MentorAgent
        note = MentorAgent._build_challenge_note(challenges)
        assert "Accept with full context" in note

    def test_challenge_note_contains_step_instructions_for_all_steps(self):
        """Each step produces distinct instructions."""
        from mentor_agent import MentorAgent
        expected = {
            1: "Flag the gap",
            2: "Investigate",
            3: "Escalate",
            4: "Challenge the shift",
            5: "Accept with full context",
        }
        for step, keyword in expected.items():
            challenges = {
                "discipline": {
                    "step": step,
                    "node_title": "Discipline",
                    "node_type": "value",
                    "permanence": "identity",
                    "history": [],
                }
            }
            note = MentorAgent._build_challenge_note(challenges)
            assert keyword in note, f"Step {step} note missing '{keyword}'"
