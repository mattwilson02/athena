"""Tests for relationship_service.py — mention tracking, health scoring, persistence."""

from __future__ import annotations

import os
import sys
import time
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.relationship_service import (
    scan_mentions,
    assess_relationship_health,
    persist_mention_stats,
    _classify_context,
    _FREQUENCY_THRESHOLDS,
)


# ── Fixtures ──


def _make_graph(persons: list[dict]) -> MagicMock:
    """Build a mock VaultGraph with the given person nodes."""
    graph = MagicMock()
    graph.get_nodes_by_type.return_value = persons
    return graph


def _make_chat_store(sessions: list[dict]) -> MagicMock:
    """Build a mock ChatStore returning the given sessions."""
    chat_store = MagicMock()
    # list_sessions returns summary dicts with 'id'
    chat_store.list_sessions.return_value = [{"id": s["id"]} for s in sessions]
    # get_session returns full session by ID
    session_map = {s["id"]: s for s in sessions}
    chat_store.get_session.side_effect = lambda sid: session_map.get(sid)
    return chat_store


def _ts(days_ago: int) -> str:
    """Return an ISO timestamp string for N days ago."""
    d = date.today() - timedelta(days=days_ago)
    return f"{d.isoformat()}T12:00:00+00:00"


def _person(pid: str, title: str, relationship: str = "friend", frequency: str = "weekly") -> dict:
    return {"id": pid, "title": title, "type": "person", "relationship": relationship, "frequency": frequency}


def _session(sid: str, messages: list[dict]) -> dict:
    return {"id": sid, "messages": messages}


def _user_msg(content: str, days_ago: int = 0) -> dict:
    return {"role": "user", "content": content, "timestamp": _ts(days_ago)}


def _asst_msg(graph_updates: list[dict], days_ago: int = 0) -> dict:
    return {"role": "assistant", "content": "ok", "graph_updates": graph_updates, "timestamp": _ts(days_ago)}


# ── scan_mentions ──


class TestScanMentions:

    def test_mention_count_basic(self):
        """Person title in 3 user messages → mention_count 3."""
        persons = [_person("ethan", "Ethan Shorthouse")]
        sessions = [
            _session("s1", [
                _user_msg("Hung out with Ethan Shorthouse today", 1),
                _user_msg("Ethan Shorthouse helped me at the gym", 2),
                _user_msg("Caught up with Ethan Shorthouse on the phone", 3),
            ]),
        ]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        assert len(result) == 1
        assert result[0]["person_id"] == "ethan"
        assert result[0]["mention_count"] == 3

    def test_mention_case_insensitive(self):
        """'ethan' or 'ethan shorthouse' matches 'Ethan Shorthouse' node."""
        persons = [_person("ethan", "Ethan Shorthouse")]
        sessions = [
            _session("s1", [
                _user_msg("talked to ethan today", 1),
                _user_msg("ethan shorthouse is great", 2),
            ]),
        ]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        assert result[0]["mention_count"] == 2

    def test_mention_graph_update_counts(self):
        """Graph update targeting a person node is counted as a mention."""
        persons = [_person("alice", "Alice")]
        update = {"action": "update", "node_id": "alice"}
        sessions = [_session("s1", [_asst_msg([update], days_ago=1)])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        assert result[0]["person_id"] == "alice"
        assert result[0]["mention_count"] == 1

    def test_no_mentions_returns_zero(self):
        """Person with no mentions → mention_count 0, last_mentioned None."""
        persons = [_person("bob", "Bob Smith")]
        sessions = [_session("s1", [_user_msg("Had a quiet day at home", 1)])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        assert result[0]["mention_count"] == 0
        assert result[0]["last_mentioned"] is None

    def test_lookback_window_filters(self):
        """Mention from 45 days ago with 30-day lookback → not counted."""
        persons = [_person("carol", "Carol")]
        sessions = [_session("s1", [
            _user_msg("Saw Carol yesterday", 45),  # outside window
            _user_msg("Unrelated message", 1),
        ])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons), lookback_days=30)
        assert result[0]["mention_count"] == 0

    def test_lookback_window_includes_recent(self):
        """Mention from 10 days ago with 30-day lookback → counted."""
        persons = [_person("carol", "Carol")]
        sessions = [_session("s1", [_user_msg("Saw Carol recently", 10)])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons), lookback_days=30)
        assert result[0]["mention_count"] == 1

    def test_context_positive(self):
        """'had a great time with Ethan' → positive context."""
        persons = [_person("ethan", "Ethan Shorthouse")]
        sessions = [_session("s1", [_user_msg("had a great time with Ethan Shorthouse", 1)])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        assert result[0]["mention_contexts"]["positive"] == 1
        assert result[0]["mention_contexts"]["negative"] == 0

    def test_context_negative(self):
        """'stressed about the argument with Sarah' → negative context."""
        persons = [_person("sarah", "Sarah")]
        sessions = [_session("s1", [_user_msg("stressed about the argument with Sarah", 1)])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        assert result[0]["mention_contexts"]["negative"] == 1

    def test_context_planning(self):
        """'planning to visit Ben next week' → planning context."""
        persons = [_person("ben", "Ben")]
        sessions = [_session("s1", [_user_msg("planning to visit Ben next week", 1)])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        assert result[0]["mention_contexts"]["planning"] == 1

    def test_context_neutral_default(self):
        """'talked to Dad' → neutral context."""
        persons = [_person("dad", "Dad")]
        sessions = [_session("s1", [_user_msg("talked to Dad earlier", 1)])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        assert result[0]["mention_contexts"]["neutral"] == 1

    def test_per_message_dedup(self):
        """Person mentioned 3 times in one message → counts as 1."""
        persons = [_person("alice", "Alice")]
        sessions = [_session("s1", [
            _user_msg("Alice said this, Alice did that, and then Alice left", 1),
        ])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        assert result[0]["mention_count"] == 1

    def test_assistant_messages_excluded(self):
        """Person name in assistant message text → not counted (only graph_updates count)."""
        persons = [_person("alice", "Alice")]
        sessions = [_session("s1", [
            {"role": "assistant", "content": "Alice sounds wonderful!", "graph_updates": [], "timestamp": _ts(1)},
        ])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        assert result[0]["mention_count"] == 0

    def test_all_persons_included(self):
        """3 person nodes, 1 mentioned → all 3 in results."""
        persons = [
            _person("alice", "Alice"),
            _person("bob", "Bob"),
            _person("carol", "Carol"),
        ]
        sessions = [_session("s1", [_user_msg("Chatted with Alice today", 1)])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        assert len(result) == 3
        ids = {r["person_id"] for r in result}
        assert ids == {"alice", "bob", "carol"}
        alice = next(r for r in result if r["person_id"] == "alice")
        assert alice["mention_count"] == 1
        for r in result:
            if r["person_id"] != "alice":
                assert r["mention_count"] == 0

    def test_graph_update_link_action_counts(self):
        """Graph update with action=link targeting a person node → counted."""
        persons = [_person("bob", "Bob")]
        update = {"action": "link", "source": "some-goal", "target": "bob"}
        sessions = [_session("s1", [_asst_msg([update], days_ago=1)])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        assert result[0]["mention_count"] == 1

    def test_no_timestamp_message_skipped(self):
        """Message without timestamp is skipped."""
        persons = [_person("alice", "Alice")]
        sessions = [_session("s1", [
            {"role": "user", "content": "Talked to Alice", "timestamp": None},
        ])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        assert result[0]["mention_count"] == 0

    def test_empty_graph_returns_empty(self):
        """No person nodes → empty results list."""
        graph = _make_graph([])
        chat_store = _make_chat_store([])
        result = scan_mentions(chat_store, graph)
        assert result == []

    def test_last_mentioned_most_recent(self):
        """last_mentioned reflects most recent mention date."""
        persons = [_person("alice", "Alice")]
        sessions = [_session("s1", [
            _user_msg("Talked to Alice", 5),
            _user_msg("Met Alice again", 1),
        ])]
        result = scan_mentions(_make_chat_store(sessions), _make_graph(persons))
        expected = (date.today() - timedelta(days=1)).isoformat()
        assert result[0]["last_mentioned"] == expected


# ── assess_relationship_health ──


class TestAssessRelationshipHealth:

    def _base_stat(self, person_id="alice", mention_count=3, days_since=2,
                   frequency="weekly", last_mentioned=None, contexts=None):
        if last_mentioned is None and days_since is not None:
            last_mentioned = (date.today() - timedelta(days=days_since)).isoformat()
        return {
            "person_id": person_id,
            "person_title": person_id.title(),
            "relationship": "friend",
            "expected_frequency": frequency,
            "mention_count": mention_count,
            "last_mentioned": last_mentioned,
            "days_since_mention": days_since,
            "mention_contexts": contexts or {"positive": 1, "negative": 0, "planning": 1, "neutral": 1},
            "recent_topics": [],
        }

    def test_health_active(self):
        """Mentioned 2 days ago, frequency weekly → active."""
        stats = [self._base_stat(days_since=2, frequency="weekly")]
        result = assess_relationship_health(stats, date.today())
        assert result[0]["health"] == "active"
        assert result[0]["drift_days"] == 0

    def test_health_drifting(self):
        """Mentioned 15 days ago, frequency weekly → drifting, drift_days=1."""
        stats = [self._base_stat(days_since=15, frequency="weekly")]
        result = assess_relationship_health(stats, date.today())
        assert result[0]["health"] == "drifting"
        assert result[0]["drift_days"] == 1  # 15 - 14 = 1

    def test_health_neglected(self):
        """Mentioned 30 days ago, frequency weekly → neglected (threshold*2=28)."""
        stats = [self._base_stat(days_since=30, frequency="weekly")]
        result = assess_relationship_health(stats, date.today())
        assert result[0]["health"] == "neglected"
        assert result[0]["drift_days"] == 16  # 30 - 14 = 16

    def test_health_no_data_inactive(self):
        """Frequency 'inactive' → no_data regardless of mentions."""
        stats = [self._base_stat(days_since=1, frequency="inactive")]
        result = assess_relationship_health(stats, date.today())
        assert result[0]["health"] == "no_data"

    def test_health_no_frequency_defaults_monthly(self):
        """No frequency field → uses monthly threshold (45 days); 10 days → active."""
        stats = [self._base_stat(days_since=10, frequency=None)]
        result = assess_relationship_health(stats, date.today())
        assert result[0]["health"] == "active"

    def test_health_no_frequency_defaults_monthly_drifting(self):
        """No frequency, 50 days since → drifting (monthly threshold=45, 50 > 45)."""
        stats = [self._base_stat(days_since=50, frequency=None)]
        result = assess_relationship_health(stats, date.today())
        assert result[0]["health"] == "drifting"

    def test_health_rare_within_threshold(self):
        """frequency: rare person not mentioned in 90 days → active (threshold=120)."""
        stats = [self._base_stat(days_since=90, frequency="rare")]
        result = assess_relationship_health(stats, date.today())
        assert result[0]["health"] == "active"

    def test_health_no_mentions_no_data(self):
        """No mentions (days_since=None) → no_data."""
        stat = self._base_stat(days_since=None, mention_count=0, last_mentioned=None)
        result = assess_relationship_health([stat], date.today())
        assert result[0]["health"] == "no_data"

    def test_influence_score_highest(self):
        """Person with most mentions → influence_score 1.0."""
        stats = [
            self._base_stat("alice", mention_count=10),
            self._base_stat("bob", mention_count=5),
        ]
        result = assess_relationship_health(stats, date.today())
        alice = next(r for r in result if r["person_id"] == "alice")
        assert alice["influence_score"] == 1.0

    def test_influence_score_relative(self):
        """5 mentions vs max 10 → influence_score 0.5."""
        stats = [
            self._base_stat("alice", mention_count=10),
            self._base_stat("bob", mention_count=5),
        ]
        result = assess_relationship_health(stats, date.today())
        bob = next(r for r in result if r["person_id"] == "bob")
        assert bob["influence_score"] == 0.5

    def test_influence_score_zero_all(self):
        """Zero mentions across all → all influence scores 0.0."""
        stats = [
            self._base_stat("alice", mention_count=0, days_since=None, last_mentioned=None),
            self._base_stat("bob", mention_count=0, days_since=None, last_mentioned=None),
        ]
        result = assess_relationship_health(stats, date.today())
        for r in result:
            assert r["influence_score"] == 0.0

    def test_influence_rank_ordering(self):
        """3 persons with different counts → ranked correctly."""
        stats = [
            self._base_stat("alice", mention_count=10),
            self._base_stat("bob", mention_count=5),
            self._base_stat("carol", mention_count=1),
        ]
        result = assess_relationship_health(stats, date.today())
        rank_map = {r["person_id"]: r["influence_rank"] for r in result}
        assert rank_map["alice"] == 1
        assert rank_map["bob"] == 2
        assert rank_map["carol"] == 3

    def test_influence_rank_ties(self):
        """Tied mention counts share the same rank."""
        stats = [
            self._base_stat("alice", mention_count=5),
            self._base_stat("bob", mention_count=5),
            self._base_stat("carol", mention_count=1),
        ]
        result = assess_relationship_health(stats, date.today())
        rank_map = {r["person_id"]: r["influence_rank"] for r in result}
        assert rank_map["alice"] == rank_map["bob"] == 1
        assert rank_map["carol"] > 1

    def test_context_profile_mostly_positive(self):
        """5 positive, 1 negative → mostly_positive."""
        stat = self._base_stat(contexts={"positive": 5, "negative": 1, "planning": 0, "neutral": 0})
        result = assess_relationship_health([stat], date.today())
        assert result[0]["context_profile"] == "mostly_positive"

    def test_context_profile_mixed(self):
        """3 positive, 3 negative → mixed."""
        stat = self._base_stat(contexts={"positive": 3, "negative": 3, "planning": 0, "neutral": 0})
        result = assess_relationship_health([stat], date.today())
        assert result[0]["context_profile"] == "mixed"

    def test_context_profile_neutral(self):
        """All neutral → neutral."""
        stat = self._base_stat(contexts={"positive": 0, "negative": 0, "planning": 0, "neutral": 5})
        result = assess_relationship_health([stat], date.today())
        assert result[0]["context_profile"] == "neutral"

    def test_context_profile_mostly_negative(self):
        """1 positive, 5 negative, 0 neutral → mostly_negative."""
        stat = self._base_stat(contexts={"positive": 1, "negative": 5, "planning": 0, "neutral": 0})
        result = assess_relationship_health([stat], date.today())
        assert result[0]["context_profile"] == "mostly_negative"

    def test_empty_stats_returns_empty(self):
        """Empty input → empty output."""
        assert assess_relationship_health([], date.today()) == []


# ── persist_mention_stats ──


class TestPersistMentionStats:

    def _make_vault_service(self, existing_node: dict | None = None) -> MagicMock:
        vs = MagicMock()
        vs.graph = MagicMock()
        vs.graph.get_node.return_value = existing_node
        vs.update.return_value = {}
        return vs

    def _rel(self, pid="alice", count=5, last=None, profile="mostly_positive"):
        return {
            "person_id": pid,
            "mention_count": count,
            "last_mentioned": last or date.today().isoformat(),
            "context_profile": profile,
        }

    def test_persist_writes_frontmatter(self):
        """Person with 5 mentions → frontmatter updated with mention_count_30d: 5."""
        vs = self._make_vault_service()
        rel = self._rel(count=5)
        result = persist_mention_stats(vs, [rel], date.today())
        assert result == 1
        vs.update.assert_called_once()
        call_args = vs.update.call_args[0][0]
        assert call_args["node_id"] == "alice"
        assert call_args["changes"]["frontmatter"]["mention_count_30d"] == 5

    def test_persist_skips_zero_mentions(self):
        """Person with 0 mentions → no update call."""
        vs = self._make_vault_service()
        rel = self._rel(count=0)
        result = persist_mention_stats(vs, [rel], date.today())
        assert result == 0
        vs.update.assert_not_called()

    def test_persist_skips_unchanged(self):
        """Same stats as existing frontmatter → no update call."""
        today_str = date.today().isoformat()
        existing = {
            "last_mentioned": today_str,
            "mention_count_30d": 5,
            "mention_profile": "mostly_positive",
        }
        vs = self._make_vault_service(existing_node=existing)
        rel = self._rel(count=5, last=today_str, profile="mostly_positive")
        result = persist_mention_stats(vs, [rel], date.today())
        assert result == 0
        vs.update.assert_not_called()

    def test_persist_returns_count(self):
        """3 persons updated → returns 3."""
        vs = self._make_vault_service()
        rels = [
            self._rel("alice", 5),
            self._rel("bob", 3),
            self._rel("carol", 7),
        ]
        result = persist_mention_stats(vs, rels, date.today())
        assert result == 3

    def test_persist_handles_missing_node(self):
        """Update raises → skipped, no error raised, returns 0."""
        vs = self._make_vault_service()
        vs.update.side_effect = Exception("node not found")
        rel = self._rel(count=5)
        # Should not raise
        result = persist_mention_stats(vs, [rel], date.today())
        assert result == 0

    def test_persist_continues_after_one_failure(self):
        """One failure → continues to update others."""
        vs = self._make_vault_service()
        vs.update.side_effect = [Exception("fail"), {}, {}]
        rels = [
            self._rel("alice", 5),
            self._rel("bob", 3),
            self._rel("carol", 7),
        ]
        result = persist_mention_stats(vs, rels, date.today())
        assert result == 2

    def test_persist_writes_mention_profile(self):
        """Mention profile is written to frontmatter."""
        vs = self._make_vault_service()
        rel = self._rel(count=5, profile="mostly_negative")
        persist_mention_stats(vs, [rel], date.today())
        call_args = vs.update.call_args[0][0]
        assert call_args["changes"]["frontmatter"]["mention_profile"] == "mostly_negative"
