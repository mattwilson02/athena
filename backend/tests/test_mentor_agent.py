"""Tests for mentor_agent.py — temporal resolution, scoring, type validation."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mentor_agent import (
    _resolve_temporal_query,
    _get_nodes_in_date_range,
    _classify_domains,
    _recency_score,
    _format_date_context,
    _bootstrap_context,
    _BOOTSTRAP_THRESHOLD,
    _STATUS_PENALTIES,
    MentorAgent,
)


# ── Temporal resolution ──


class TestResolveTemporalQuery:
    """Test _resolve_temporal_query with fixed dates."""

    @pytest.fixture(autouse=True)
    def freeze_date(self):
        """Fix date.today() to Wednesday 2026-02-25."""
        frozen = date(2026, 2, 25)  # Wednesday
        with patch("mentor_agent.date") as mock_date:
            mock_date.today.return_value = frozen
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            yield frozen

    def test_today(self):
        result = _resolve_temporal_query("what do I have today")
        assert result == (date(2026, 2, 25), date(2026, 2, 25))

    def test_tonight(self):
        result = _resolve_temporal_query("plans for tonight")
        assert result == (date(2026, 2, 25), date(2026, 2, 25))

    def test_tomorrow(self):
        result = _resolve_temporal_query("what's happening tomorrow")
        assert result == (date(2026, 2, 26), date(2026, 2, 26))

    def test_yesterday(self):
        result = _resolve_temporal_query("what did I do yesterday")
        assert result == (date(2026, 2, 24), date(2026, 2, 24))

    def test_this_weekend(self):
        # Wednesday → Saturday Feb 28 + Sunday Mar 1
        result = _resolve_temporal_query("full breakdown of this weekend")
        assert result == (date(2026, 2, 28), date(2026, 3, 1))

    def test_next_weekend(self):
        # Wednesday → next Saturday Mar 7 + Sunday Mar 8
        result = _resolve_temporal_query("what about next weekend")
        assert result == (date(2026, 3, 7), date(2026, 3, 8))

    def test_this_week(self):
        # Wednesday → Monday Feb 23 to Sunday Mar 1
        result = _resolve_temporal_query("overview of this week")
        assert result == (date(2026, 2, 23), date(2026, 3, 1))

    def test_next_week(self):
        # Wednesday → Monday Mar 2 to Sunday Mar 8
        result = _resolve_temporal_query("plans for next week")
        assert result == (date(2026, 3, 2), date(2026, 3, 8))

    def test_this_month(self):
        result = _resolve_temporal_query("what happened this month")
        assert result == (date(2026, 2, 1), date(2026, 2, 28))

    def test_day_name_friday(self):
        # Wednesday → next Friday = Feb 27
        result = _resolve_temporal_query("what's on friday")
        assert result == (date(2026, 2, 27), date(2026, 2, 27))

    def test_day_name_saturday(self):
        # Wednesday → next Saturday = Feb 28
        result = _resolve_temporal_query("saturday plans")
        assert result == (date(2026, 2, 28), date(2026, 2, 28))

    def test_day_name_monday(self):
        # Wednesday → next Monday = Mar 2
        result = _resolve_temporal_query("monday meeting")
        assert result == (date(2026, 3, 2), date(2026, 3, 2))

    def test_no_temporal_phrase(self):
        result = _resolve_temporal_query("tell me about my goals")
        assert result is None

    def test_case_insensitive(self):
        result = _resolve_temporal_query("What's happening TODAY?")
        assert result == (date(2026, 2, 25), date(2026, 2, 25))


# ── Date range matching ──


class TestGetNodesInDateRange:
    """Test _get_nodes_in_date_range with mock graph."""

    class FakeGraph:
        def __init__(self, nodes):
            self._nodes = nodes

        def get_all_nodes(self):
            return self._nodes

    def test_matches_date_field(self):
        nodes = [
            {"id": "task-1", "date": "2026-02-28"},
            {"id": "task-2", "date": "2026-03-01"},
            {"id": "task-3", "date": "2026-03-05"},
        ]
        graph = self.FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, date(2026, 2, 28), date(2026, 3, 1))
        ids = [n["id"] for n in result]
        assert "task-1" in ids
        assert "task-2" in ids
        assert "task-3" not in ids

    def test_matches_due_field(self):
        nodes = [{"id": "task-1", "due": "2026-02-28"}]
        graph = self.FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, date(2026, 2, 28), date(2026, 2, 28))
        assert len(result) == 1

    def test_matches_deadline_field(self):
        nodes = [{"id": "task-1", "deadline": "2026-02-28"}]
        graph = self.FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, date(2026, 2, 28), date(2026, 2, 28))
        assert len(result) == 1

    def test_ignores_invalid_dates(self):
        nodes = [{"id": "task-1", "date": "not-a-date"}]
        graph = self.FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, date(2026, 2, 1), date(2026, 2, 28))
        assert len(result) == 0

    def test_no_date_fields(self):
        nodes = [{"id": "goal-1", "title": "Learn piano"}]
        graph = self.FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, date(2026, 2, 1), date(2026, 2, 28))
        assert len(result) == 0

    def test_date_object_values(self):
        nodes = [{"id": "task-1", "date": date(2026, 2, 28)}]
        graph = self.FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, date(2026, 2, 28), date(2026, 2, 28))
        assert len(result) == 1

    def test_no_double_match(self):
        """A node with date AND due in range should only appear once."""
        nodes = [{"id": "task-1", "date": "2026-02-28", "due": "2026-02-28"}]
        graph = self.FakeGraph(nodes)
        result = _get_nodes_in_date_range(graph, date(2026, 2, 28), date(2026, 2, 28))
        assert len(result) == 1


# ── Domain classification ──


class TestClassifyDomains:
    def test_planning_keywords(self):
        domains = _classify_domains("what tasks do I have this weekend")
        assert "Planning" in domains

    def test_self_keywords(self):
        domains = _classify_domains("what are my goals")
        assert "Self" in domains

    def test_people_keywords(self):
        domains = _classify_domains("tell me about my friend Alice")
        assert "People" in domains

    def test_no_match(self):
        domains = _classify_domains("xyzzy qwerty")
        assert domains == []


# ── Type validation ──


class TestValidateTypes:
    """Test _validate_types with all action types."""

    @pytest.fixture
    def agent(self):
        """Build a MentorAgent with mocked dependencies."""
        from unittest.mock import MagicMock
        schema = {
            "type_list": ["goal", "task", "person", "note", "event"],
            "types": {},
        }
        graph = MagicMock()
        vector_index = MagicMock()
        client = MagicMock()
        agent = MentorAgent.__new__(MentorAgent)
        agent.schema = schema
        agent.graph = graph
        agent.vector_index = vector_index
        agent.client = client
        return agent

    def test_valid_create_passes(self, agent):
        updates = [{"action": "create", "type": "goal", "node_id": "test"}]
        result = agent._validate_types(updates)
        assert result[0]["type"] == "goal"

    def test_invalid_create_falls_back_to_note(self, agent):
        updates = [{"action": "create", "type": "spectator", "node_id": "test"}]
        result = agent._validate_types(updates)
        assert result[0]["type"] == "note"

    def test_case_insensitive_create(self, agent):
        updates = [{"action": "create", "type": "GOAL", "node_id": "test"}]
        result = agent._validate_types(updates)
        assert result[0]["type"] == "goal"

    def test_invalid_edge_on_create(self, agent):
        updates = [{"action": "create", "type": "goal", "node_id": "test",
                     "edges": [{"target": "x", "type": "yolo"}]}]
        result = agent._validate_types(updates)
        assert result[0]["edges"][0]["type"] == "relates_to"

    def test_valid_edge_on_create(self, agent):
        updates = [{"action": "create", "type": "goal", "node_id": "test",
                     "edges": [{"target": "x", "type": "blocked_by"}]}]
        result = agent._validate_types(updates)
        assert result[0]["edges"][0]["type"] == "blocked_by"

    def test_invalid_type_in_update_frontmatter(self, agent):
        updates = [{"action": "update", "node_id": "test",
                     "changes": {"frontmatter": {"type": "spectator"}}}]
        result = agent._validate_types(updates)
        assert "type" not in result[0]["changes"]["frontmatter"]

    def test_valid_type_in_update_frontmatter(self, agent):
        updates = [{"action": "update", "node_id": "test",
                     "changes": {"frontmatter": {"type": "task"}}}]
        result = agent._validate_types(updates)
        assert result[0]["changes"]["frontmatter"]["type"] == "task"

    def test_invalid_edge_in_update_add_edges(self, agent):
        updates = [{"action": "update", "node_id": "test",
                     "changes": {"add_edges": [{"target": "x", "type": "yolo"}]}}]
        result = agent._validate_types(updates)
        assert result[0]["changes"]["add_edges"][0]["type"] == "relates_to"

    def test_invalid_link_type(self, agent):
        updates = [{"action": "link", "source": "a", "target": "b", "type": "yolo"}]
        result = agent._validate_types(updates)
        assert result[0]["type"] == "relates_to"

    def test_valid_link_type(self, agent):
        updates = [{"action": "link", "source": "a", "target": "b", "type": "part_of"}]
        result = agent._validate_types(updates)
        assert result[0]["type"] == "part_of"

    def test_mixed_actions(self, agent):
        updates = [
            {"action": "create", "type": "goal", "node_id": "a",
             "edges": [{"target": "b", "type": "supported_by"}]},
            {"action": "update", "node_id": "b",
             "changes": {"add_edges": [{"target": "c", "type": "garbage"}]}},
            {"action": "link", "source": "c", "target": "d", "type": "involves"},
        ]
        result = agent._validate_types(updates)
        assert result[0]["type"] == "goal"
        assert result[0]["edges"][0]["type"] == "supported_by"
        assert result[1]["changes"]["add_edges"][0]["type"] == "relates_to"
        assert result[2]["type"] == "involves"


# ── Recency scoring ──


class TestRecencyScore:
    def test_today_is_1(self):
        node = {"updated": date.today().isoformat()}
        assert _recency_score(node) == pytest.approx(1.0)

    def test_future_date_is_1(self):
        future = date.today() + timedelta(days=5)
        node = {"date": future.isoformat()}
        assert _recency_score(node) == pytest.approx(1.0)

    def test_90_days_ago_is_0(self):
        old = date.today() - timedelta(days=91)
        node = {"created": old.isoformat()}
        assert _recency_score(node) == pytest.approx(0.0)

    def test_no_date_is_0(self):
        assert _recency_score({"id": "test"}) == pytest.approx(0.0)


# ── Status penalties ──


class TestStatusPenalties:
    def test_completed_penalty(self):
        assert _STATUS_PENALTIES["completed"] == -0.15

    def test_cancelled_penalty(self):
        assert _STATUS_PENALTIES["cancelled"] == -0.25

    def test_parked_penalty(self):
        assert _STATUS_PENALTIES["parked"] == -0.10

    def test_active_no_penalty(self):
        assert _STATUS_PENALTIES.get("active", 0.0) == 0.0

    def test_no_status_no_penalty(self):
        assert _STATUS_PENALTIES.get("", 0.0) == 0.0


# ── Pre-computed temporal context ──


class TestFormatDateContext:
    TODAY = date(2026, 2, 28)

    def test_future_date(self):
        node = {"date": "2026-05-09"}
        result = _format_date_context(node, today=self.TODAY)
        assert "in 70 days" in result
        assert "Date: 2026-05-09" in result

    def test_past_date(self):
        node = {"created": "2026-02-25"}
        result = _format_date_context(node, today=self.TODAY)
        assert "3 days ago" in result

    def test_today(self):
        node = {"date": "2026-02-28"}
        result = _format_date_context(node, today=self.TODAY)
        assert "today" in result

    def test_yesterday(self):
        node = {"date": "2026-02-27"}
        result = _format_date_context(node, today=self.TODAY)
        assert "yesterday" in result

    def test_no_date_fields(self):
        node = {"id": "test", "title": "Test"}
        result = _format_date_context(node, today=self.TODAY)
        assert result == ""

    def test_date_object(self):
        node = {"date": date(2026, 3, 1)}
        result = _format_date_context(node, today=self.TODAY)
        assert "tomorrow" in result

    def test_multiple_fields(self):
        node = {"date": "2026-05-09", "created": "2026-02-25"}
        result = _format_date_context(node, today=self.TODAY)
        assert "Date:" in result
        assert "Created:" in result


# ── Bootstrap conversation ──


class TestBootstrapContext:
    """Test _bootstrap_context with empty and partial vaults."""

    class FakeGraph:
        def __init__(self, nodes):
            self._nodes = nodes

        def get_all_nodes(self):
            return self._nodes

    def test_empty_vault_returns_bootstrap_prompt(self):
        graph = self.FakeGraph([])
        result = _bootstrap_context(0, graph)
        assert "BOOTSTRAP MODE" in result
        assert "brand new vault" in result
        assert "no nodes" in result

    def test_partial_vault_lists_existing_nodes(self):
        nodes = [
            {"id": "discipline", "title": "Discipline", "type": "value"},
            {"id": "run-marathon", "title": "Run a Marathon", "type": "goal"},
        ]
        graph = self.FakeGraph(nodes)
        result = _bootstrap_context(2, graph)
        assert "BOOTSTRAP MODE" in result
        assert "2 node(s)" in result
        assert '"Discipline" (value)' in result
        assert '"Run a Marathon" (goal)' in result

    def test_partial_vault_mentions_gaps(self):
        nodes = [{"id": "test", "title": "Test", "type": "goal"}]
        graph = self.FakeGraph(nodes)
        result = _bootstrap_context(1, graph)
        assert "gaps" in result.lower() or "missing" in result.lower()

    def test_threshold_is_10(self):
        assert _BOOTSTRAP_THRESHOLD == 10


class TestBootstrapInGetContext:
    """Test that get_context returns bootstrap prompt when vault is small."""

    def _make_agent(self, nodes):
        from unittest.mock import MagicMock

        class FakeGraph:
            def __init__(self, nodes):
                self._nodes = nodes

            def get_all_nodes(self):
                return self._nodes

            def get_node(self, nid):
                return next((n for n in self._nodes if n["id"] == nid), None)

            def get_neighbors(self, nid, depth=1):
                return []

            def get_neighbors_by_hop(self, nid, depth=2):
                return {}

            def get_degree(self, nid):
                return 0

        schema = {"type_list": ["goal", "value", "fear"], "types": {}}
        graph = FakeGraph(nodes)
        vector_index = MagicMock()
        vector_index.search.return_value = []
        client = MagicMock()

        agent = MentorAgent.__new__(MentorAgent)
        agent.graph = graph
        agent.vector_index = vector_index
        agent.schema = schema
        agent.client = client
        agent.system_prompt_template = "test {context} {today}"
        agent.model = "test-model"
        return agent

    def test_empty_vault_triggers_bootstrap(self):
        agent = self._make_agent([])
        context, results = agent.get_context("hello")
        assert "BOOTSTRAP MODE" in context
        assert results == []

    def test_partial_vault_triggers_bootstrap(self):
        nodes = [{"id": f"node-{i}", "title": f"Node {i}", "type": "goal"} for i in range(5)]
        agent = self._make_agent(nodes)
        context, results = agent.get_context("hello")
        assert "BOOTSTRAP MODE" in context
        assert "5 node(s)" in context

    def test_full_vault_skips_bootstrap(self):
        nodes = [{"id": f"node-{i}", "title": f"Node {i}", "type": "goal"} for i in range(15)]
        agent = self._make_agent(nodes)
        context, results = agent.get_context("hello")
        assert "BOOTSTRAP MODE" not in context


class TestDismissedNote:
    """Test that _build_dismissed_note produces correct output."""

    def _make_agent(self):
        from unittest.mock import MagicMock
        agent = MentorAgent.__new__(MentorAgent)
        agent.graph = MagicMock()
        agent.vector_index = MagicMock()
        agent.schema = {"type_list": ["goal"], "types": {}}
        agent.client = MagicMock()
        agent.system_prompt_template = "test {context} {today}"
        agent.model = "test-model"
        return agent

    def test_empty_dismissed_returns_empty(self):
        agent = self._make_agent()
        assert agent._build_dismissed_note([]) == ""

    def test_none_dismissed_returns_empty(self):
        agent = self._make_agent()
        assert agent._build_dismissed_note([]) == ""

    def test_dismissed_ids_in_note(self):
        agent = self._make_agent()
        note = agent._build_dismissed_note(["guitar-project", "daily-scales"])
        assert "DISMISSED PROPOSALS" in note
        assert "guitar-project" in note
        assert "daily-scales" in note
        assert "do NOT reference" in note.lower() or "do NOT reference" in note

    def test_caps_at_10(self):
        agent = self._make_agent()
        ids = [f"node-{i}" for i in range(15)]
        note = agent._build_dismissed_note(ids)
        # Should only include the last 10 (most recent)
        assert "node-5" in note
        assert "node-14" in note
        assert "node-0" not in note
        assert "node-4" not in note
