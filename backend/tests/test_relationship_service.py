"""Tests for relationship_service.py — mention scanning, person intelligence, social patterns."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.relationship_service import (
    scan_person_mentions,
    get_person_intelligence,
    detect_social_patterns,
)


# ── Helpers ──


def make_message(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def make_person(node_id: str, title: str, relationship: str = "friend", **kwargs) -> dict:
    node = {"id": node_id, "type": "person", "title": title, "relationship": relationship}
    node.update(kwargs)
    return node


class FakeGraph:
    """Minimal graph stub for testing get_person_intelligence and detect_social_patterns."""

    def __init__(self, nodes=None, neighbor_map=None):
        self._nodes = {n["id"]: n for n in (nodes or [])}
        self._neighbor_map = neighbor_map or {}

    def get_nodes_by_type(self, node_type: str) -> list[dict]:
        return [dict(n) for n in self._nodes.values() if n.get("type") == node_type]

    def get_node(self, node_id: str) -> dict | None:
        n = self._nodes.get(node_id)
        return dict(n) if n else None

    def get_neighbors_with_edges(self, node_id: str) -> list[dict]:
        return list(self._neighbor_map.get(node_id, []))

    def get_all_nodes(self) -> list[dict]:
        return [dict(n) for n in self._nodes.values()]


# ── scan_person_mentions ──


class TestScanPersonMentions:

    def test_mention_detection_basic(self):
        """Message containing 'Ben' matches person node with title 'Ben'."""
        messages = [make_message("user", "Ben and I are planning to start a company.")]
        persons = [make_person("ben", "Ben")]
        results = scan_person_mentions(messages, persons)
        assert len(results) == 1
        assert results[0]["person_id"] == "ben"
        assert results[0]["mention_count"] == 1

    def test_mention_detection_word_boundary(self):
        """'benefit' does NOT match person 'Ben'."""
        messages = [make_message("user", "The benefit of this plan is great.")]
        persons = [make_person("ben", "Ben")]
        results = scan_person_mentions(messages, persons)
        assert len(results) == 0

    def test_mention_count_across_messages(self):
        """3 messages mentioning 'Ben' → mention_count 3."""
        messages = [
            make_message("user", "Ben called today."),
            make_message("user", "I need to reply to Ben."),
            make_message("user", "Ben and I are co-founders."),
        ]
        persons = [make_person("ben", "Ben")]
        results = scan_person_mentions(messages, persons)
        assert results[0]["mention_count"] == 3

    def test_mention_context_extracted(self):
        """Context snippet captures surrounding text."""
        messages = [make_message("user", "Ben and I are planning to start Citadel Technica.")]
        persons = [make_person("ben", "Ben")]
        results = scan_person_mentions(messages, persons)
        assert len(results[0]["contexts"]) == 1
        assert "Ben" in results[0]["contexts"][0]

    def test_sentiment_positive(self):
        """Mention with 'great' and 'excited' → sentiment 'positive'."""
        messages = [make_message("user", "I'm so excited to work with Ben, it's great!")]
        persons = [make_person("ben", "Ben")]
        results = scan_person_mentions(messages, persons)
        assert results[0]["sentiment"] == "positive"

    def test_sentiment_negative(self):
        """Mention with 'frustrated' → sentiment 'negative'."""
        messages = [make_message("user", "I'm really frustrated with Ben today.")]
        persons = [make_person("ben", "Ben")]
        results = scan_person_mentions(messages, persons)
        assert results[0]["sentiment"] == "negative"

    def test_sentiment_neutral_default(self):
        """Mention with no signal words → 'neutral'."""
        messages = [make_message("user", "Ben called about the meeting on Thursday.")]
        persons = [make_person("ben", "Ben")]
        results = scan_person_mentions(messages, persons)
        assert results[0]["sentiment"] == "neutral"

    def test_sentiment_mixed(self):
        """Both positive and negative signals → 'mixed'."""
        messages = [make_message("user", "I love working with Ben but I'm frustrated sometimes.")]
        persons = [make_person("ben", "Ben")]
        results = scan_person_mentions(messages, persons)
        assert results[0]["sentiment"] == "mixed"

    def test_no_person_nodes_empty_result(self):
        """No person nodes → empty list."""
        messages = [make_message("user", "Ben called today.")]
        results = scan_person_mentions(messages, [])
        assert results == []

    def test_only_user_messages_scanned(self):
        """Assistant message mentioning a person → not counted."""
        messages = [
            make_message("assistant", "Ben is your co-founder and he called today."),
            make_message("user", "Yes, I know."),
        ]
        persons = [make_person("ben", "Ben")]
        results = scan_person_mentions(messages, persons)
        assert len(results) == 0

    def test_empty_messages_empty_result(self):
        """Empty messages list → empty result."""
        persons = [make_person("ben", "Ben")]
        results = scan_person_mentions([], persons)
        assert results == []

    def test_context_capped_at_three(self):
        """No more than 3 context snippets per person."""
        messages = [
            make_message("user", f"Message {i} about Ben")
            for i in range(5)
        ]
        persons = [make_person("ben", "Ben")]
        results = scan_person_mentions(messages, persons)
        assert len(results[0]["contexts"]) <= 3

    def test_multiword_person_first_name_match(self):
        """'Harry Heppleston' can be matched by just 'Harry'."""
        messages = [make_message("user", "Caught up with Harry yesterday.")]
        persons = [make_person("harry-h", "Harry Heppleston")]
        results = scan_person_mentions(messages, persons)
        assert len(results) == 1
        assert results[0]["person_id"] == "harry-h"

    def test_case_insensitive_match(self):
        """Matching is case-insensitive."""
        messages = [make_message("user", "Spoke with BEN about the project.")]
        persons = [make_person("ben", "Ben")]
        results = scan_person_mentions(messages, persons)
        assert results[0]["mention_count"] == 1

    def test_relationship_field_populated(self):
        """relationship field comes from person node frontmatter."""
        messages = [make_message("user", "Ben called.")]
        persons = [make_person("ben", "Ben", relationship="colleague")]
        results = scan_person_mentions(messages, persons)
        assert results[0]["relationship"] == "colleague"


# ── get_person_intelligence ──


class TestGetPersonIntelligence:

    def test_person_intelligence_days_since(self):
        """Person updated 7 days ago → days_since_update 7."""
        today = date(2026, 3, 23)
        updated = (today - timedelta(days=7)).isoformat()
        person = {"id": "ben", "type": "person", "title": "Ben", "updated": updated}
        graph = FakeGraph(nodes=[person])
        results = get_person_intelligence(graph, today)
        assert len(results) == 1
        assert results[0]["days_since_update"] == 7

    def test_person_intelligence_connected_counts(self):
        """Person with 3 active, 2 completed neighbors → connected_active_count 3."""
        today = date(2026, 3, 23)
        person = {"id": "ben", "type": "person", "title": "Ben", "updated": "2026-03-20"}
        active_neighbors = [
            {"id": f"proj-{i}", "type": "project", "status": "active", "_edge_type": "involves"}
            for i in range(3)
        ]
        completed_neighbors = [
            {"id": f"task-{i}", "type": "task", "status": "completed", "_edge_type": "involves"}
            for i in range(2)
        ]
        graph = FakeGraph(
            nodes=[person],
            neighbor_map={"ben": active_neighbors + completed_neighbors},
        )
        results = get_person_intelligence(graph, today)
        assert results[0]["connected_node_count"] == 5
        assert results[0]["connected_active_count"] == 3

    def test_person_intelligence_no_updated_date(self):
        """Person with no updated date → days_since_update None."""
        today = date(2026, 3, 23)
        person = {"id": "ben", "type": "person", "title": "Ben"}
        graph = FakeGraph(nodes=[person])
        results = get_person_intelligence(graph, today)
        assert results[0]["days_since_update"] is None
        assert results[0]["last_updated"] is None

    def test_person_intelligence_connection_types(self):
        """connection_types lists unique types of connected nodes."""
        today = date(2026, 3, 23)
        person = {"id": "ben", "type": "person", "title": "Ben", "updated": "2026-03-20"}
        neighbors = [
            {"id": "proj-1", "type": "project", "status": "active", "_edge_type": "involves"},
            {"id": "event-1", "type": "event", "status": "active", "_edge_type": "involves"},
            {"id": "proj-2", "type": "project", "status": "active", "_edge_type": "involves"},
        ]
        graph = FakeGraph(nodes=[person], neighbor_map={"ben": neighbors})
        results = get_person_intelligence(graph, today)
        assert set(results[0]["connection_types"]) == {"project", "event"}

    def test_person_intelligence_empty_graph(self):
        """No person nodes → empty result."""
        graph = FakeGraph()
        results = get_person_intelligence(graph, date.today())
        assert results == []


# ── detect_social_patterns ──


class TestDetectSocialPatterns:

    def _make_intel(self, person_id: str, days_since: int | None) -> dict:
        return {
            "person_id": person_id,
            "person_title": person_id.capitalize(),
            "relationship": "friend",
            "last_updated": None,
            "days_since_update": days_since,
            "connected_node_count": 1,
            "connected_active_count": 1,
            "connection_types": ["project"],
        }

    def _make_fundamentals(self, status: str = "active") -> list[dict]:
        return [{"fundamental": "connection", "status": status, "days_since_activity": 21 if status == "neglected" else 5}]

    def test_social_pattern_no_data(self):
        """No person nodes → 'no_data' pattern."""
        graph = FakeGraph()
        result = detect_social_patterns([], [], graph, date.today())
        assert result["pattern"] == "no_data"

    def test_social_pattern_healthy(self):
        """Recent updates, connection active → 'healthy'."""
        today = date(2026, 3, 23)
        intel = [self._make_intel(f"person-{i}", 5) for i in range(5)]
        fundamentals = self._make_fundamentals("active")
        graph = FakeGraph(nodes=[{"id": p["person_id"], "type": "person", "title": p["person_title"]} for p in intel])
        result = detect_social_patterns(intel, fundamentals, graph, today)
        assert result["pattern"] == "healthy"

    def test_social_pattern_isolating(self):
        """80% stale + connection neglected → 'isolating'."""
        today = date(2026, 3, 23)
        # 8 stale (40+ days), 2 recent
        intel = (
            [self._make_intel(f"stale-{i}", 40) for i in range(8)]
            + [self._make_intel(f"recent-{i}", 5) for i in range(2)]
        )
        fundamentals = self._make_fundamentals("neglected")
        all_nodes = [{"id": p["person_id"], "type": "person", "title": p["person_title"]} for p in intel]
        graph = FakeGraph(nodes=all_nodes)
        result = detect_social_patterns(intel, fundamentals, graph, today)
        assert result["pattern"] == "isolating"
        assert result["confidence"] in ("medium", "high")

    def test_social_pattern_overcommitting(self):
        """6 social events this week, no isolation → 'overcommitting'."""
        today = date(2026, 3, 23)
        intel = [self._make_intel(f"person-{i}", 5) for i in range(3)]
        fundamentals = self._make_fundamentals("active")

        # Build graph with 6 upcoming events that have person edges.
        # Include 'updated' field so they also count as recent social nodes
        # (preventing a false low_social_activity isolation signal).
        event_nodes = []
        neighbor_map = {}
        person_nodes = [{"id": p["person_id"], "type": "person", "title": p["person_title"]} for p in intel]

        for i in range(6):
            due = (today + timedelta(days=i % 7)).isoformat()
            event_id = f"event-{i}"
            event_nodes.append({
                "id": event_id, "type": "event", "status": "active",
                "due": due, "updated": today.isoformat(),
            })
            neighbor_map[event_id] = [{"id": "person-0", "type": "person", "_edge_type": "involves"}]

        all_nodes = person_nodes + event_nodes
        graph = FakeGraph(nodes=all_nodes, neighbor_map=neighbor_map)
        result = detect_social_patterns(intel, fundamentals, graph, today)
        assert result["pattern"] == "overcommitting"

    def test_social_pattern_confidence_scales(self):
        """2 signals → 'medium' confidence, 3 → 'high'."""
        today = date(2026, 3, 23)

        # 2 signals: stale ratio >= 0.6 + connection neglected.
        # Add 2 recent social events with person edges to suppress low_social_activity signal
        # (spec: <2 recent social nodes = isolation signal; >=2 suppresses it).
        intel_2 = [self._make_intel(f"stale-{i}", 40) for i in range(10)]
        person_nodes_2 = [{"id": p["person_id"], "type": "person", "title": p["person_title"]} for p in intel_2]
        # Need >4 recent social nodes to suppress the low_social_activity signal (score=0.0)
        recent_events = [
            {"id": f"recent-event-{j}", "type": "event", "status": "active", "updated": today.isoformat()}
            for j in range(5)
        ]
        neighbor_map_2 = {
            f"recent-event-{j}": [{"id": "stale-0", "type": "person", "_edge_type": "involves"}]
            for j in range(5)
        }
        graph_2 = FakeGraph(nodes=person_nodes_2 + recent_events, neighbor_map=neighbor_map_2)
        result_2 = detect_social_patterns(intel_2, self._make_fundamentals("neglected"), graph_2, today)
        assert result_2["confidence"] == "medium"

        # 3 signals: stale ratio + connection neglected + low_social_activity (no social nodes)
        graph_3 = FakeGraph(nodes=person_nodes_2)
        result_3 = detect_social_patterns(intel_2, self._make_fundamentals("neglected"), graph_3, today)
        assert result_3["confidence"] == "high"

    def test_stale_relationships_listed(self):
        """Person nodes >30 days stale appear in stale_relationships."""
        today = date(2026, 3, 23)
        intel = [
            self._make_intel("stale-person", 45),
            self._make_intel("recent-person", 3),
        ]
        graph = FakeGraph(nodes=[{"id": p["person_id"], "type": "person", "title": p["person_title"]} for p in intel])
        result = detect_social_patterns(intel, [], graph, today)
        stale_ids = [r["person_id"] for r in result["stale_relationships"]]
        assert "stale-person" in stale_ids
        assert "recent-person" not in stale_ids

    def test_active_relationships_listed(self):
        """Person nodes <14 days appear in active_relationships."""
        today = date(2026, 3, 23)
        intel = [
            self._make_intel("stale-person", 45),
            self._make_intel("recent-person", 3),
        ]
        graph = FakeGraph(nodes=[{"id": p["person_id"], "type": "person", "title": p["person_title"]} for p in intel])
        result = detect_social_patterns(intel, [], graph, today)
        active_ids = [r["person_id"] for r in result["active_relationships"]]
        assert "recent-person" in active_ids
        assert "stale-person" not in active_ids

    def test_inactive_person_excluded(self):
        """Person with status 'inactive' not counted in pattern detection."""
        today = date(2026, 3, 23)
        intel = [self._make_intel("inactive-person", 100)]
        graph = FakeGraph(nodes=[
            {"id": "inactive-person", "type": "person", "title": "Inactive", "status": "inactive"}
        ])
        result = detect_social_patterns(intel, [], graph, today)
        assert result["pattern"] == "no_data"
