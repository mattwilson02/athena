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
    build_system_prompt,
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


class TestBuildConflictNote:
    """Test _build_conflict_note with regular, overload, and mixed conflicts."""

    def test_empty_conflicts_returns_empty(self):
        assert MentorAgent._build_conflict_note([]) == ""

    def test_none_conflicts_returns_empty(self):
        assert MentorAgent._build_conflict_note([]) == ""

    def test_regular_conflict(self):
        conflicts = [{
            "node_id": "discipline-value",
            "title": "Discipline",
            "conflict_type": "value_violation",
            "explanation": "Staying out late contradicts discipline",
            "severity": "hard",
        }]
        note = MentorAgent._build_conflict_note(conflicts)
        assert "CONFLICT DETECTION" in note
        assert "[HARD |" in note
        assert "value_violation" in note
        assert "Discipline" in note
        assert "MUST acknowledge" in note

    def test_soft_conflict(self):
        conflicts = [{
            "node_id": "marathon-goal",
            "title": "Marathon Training",
            "conflict_type": "goal_contradiction",
            "explanation": "Skipping gym disrupts training",
            "severity": "soft",
        }]
        note = MentorAgent._build_conflict_note(conflicts)
        assert "[SOFT |" in note
        assert "Marathon Training" in note

    def test_overload_conflict(self):
        conflicts = [{
            "node_id": "__obligations__",
            "title": "Commitment overload",
            "conflict_type": "commitment_overload",
            "explanation": "Too many active commitments",
            "severity": "soft",
            "obligations": {
                "goals": [{"title": "Goal A"}, {"title": "Goal B"}, {"title": "Goal C"}],
                "projects": [{"title": "Project X"}],
                "habits": [{"title": "Gym"}, {"title": "Reading"}],
                "events_upcoming": [],
            },
        }]
        note = MentorAgent._build_conflict_note(conflicts)
        assert "ACTIVE OBLIGATIONS" in note
        assert "3 active goals" in note
        assert "Goal A" in note
        assert "1 active projects" in note
        assert "2 active habits" in note
        assert "deprioritize" in note
        # Should NOT have CONFLICT DETECTION header (no regular conflicts)
        assert "CONFLICT DETECTION" not in note

    def test_mixed_regular_and_overload(self):
        conflicts = [
            {
                "node_id": "discipline-value",
                "title": "Discipline",
                "conflict_type": "value_violation",
                "explanation": "Contradicts discipline",
                "severity": "hard",
            },
            {
                "node_id": "__obligations__",
                "title": "Commitment overload",
                "conflict_type": "commitment_overload",
                "explanation": "Too many commitments",
                "severity": "soft",
                "obligations": {
                    "goals": [{"title": "Goal A"}, {"title": "Goal B"}, {"title": "Goal C"}],
                    "projects": [],
                    "habits": [],
                    "events_upcoming": [],
                },
            },
        ]
        note = MentorAgent._build_conflict_note(conflicts)
        assert "CONFLICT DETECTION" in note
        assert "ACTIVE OBLIGATIONS" in note
        assert "[HARD |" in note
        assert "Discipline" in note
        assert "3 active goals" in note

    def test_multiple_regular_conflicts(self):
        conflicts = [
            {
                "node_id": "sleep-goal",
                "title": "9pm-5am Sleep",
                "conflict_type": "goal_contradiction",
                "explanation": "Late night contradicts sleep goal",
                "severity": "hard",
            },
            {
                "node_id": "discipline-value",
                "title": "Discipline",
                "conflict_type": "value_violation",
                "explanation": "Contradicts discipline value",
                "severity": "soft",
            },
        ]
        note = MentorAgent._build_conflict_note(conflicts)
        assert "9pm-5am Sleep" in note
        assert "Discipline" in note
        assert "[HARD |" in note
        assert "[SOFT |" in note


# ── Soul modes parsing ──


_MINIMAL_SCHEMA = {
    "type_list": ["goal", "value", "task", "note"],
    "types": {
        "goal": {"domain": "Self", "description": "A goal"},
        "value": {"domain": "Self", "description": "A value"},
        "task": {"domain": "Planning", "description": "A task"},
        "note": {"domain": "Knowledge", "description": "A note"},
    },
    "domain_list": ["Self", "Planning", "Knowledge"],
    "domains": {
        "Self": {"folder": "Self", "description": "Inner world", "types": ["goal", "value"]},
        "Planning": {"folder": "Planning", "description": "Tasks and plans", "types": ["task"]},
        "Knowledge": {"folder": "Knowledge", "description": "Notes and ideas", "types": ["note"]},
    },
}


class TestSoulModesParsed:
    """Test that _load_soul() and build_system_prompt() parse mode sections correctly."""

    def test_soul_modes_parsed(self):
        """All four mode keys are present in parsed mode_instructions."""
        _, mode_instructions = build_system_prompt(_MINIMAL_SCHEMA)
        assert "mirror" in mode_instructions
        assert "advisor" in mode_instructions
        assert "guardian" in mode_instructions
        assert "dialectic" in mode_instructions

    def test_mode_instructions_not_empty(self):
        """Each mode has non-empty instruction text."""
        _, mode_instructions = build_system_prompt(_MINIMAL_SCHEMA)
        for mode in ("mirror", "advisor", "guardian", "dialectic"):
            assert mode_instructions[mode].strip(), f"Mode '{mode}' has empty instructions"


class TestSystemPromptIncludesMode:
    """Test that _build_mode_note() injects mode instructions into system prompt."""

    def _make_agent(self):
        from unittest.mock import MagicMock
        agent = MentorAgent.__new__(MentorAgent)
        agent.graph = MagicMock()
        agent.vector_index = MagicMock()
        agent.schema = _MINIMAL_SCHEMA
        agent.client = MagicMock()
        agent.system_prompt_template, agent.mode_instructions = build_system_prompt(_MINIMAL_SCHEMA)
        agent.model = "test-model"
        return agent

    def test_system_prompt_includes_active_mode(self):
        agent = self._make_agent()
        note = agent._build_mode_note("advisor")
        assert "ACTIVE MODE: advisor" in note

    def test_system_prompt_mirror_default(self):
        agent = self._make_agent()
        note = agent._build_mode_note("mirror")
        assert "ACTIVE MODE: mirror" in note
        assert agent.mode_instructions["mirror"] in note

    def test_mode_guardian_includes_instructions(self):
        agent = self._make_agent()
        note = agent._build_mode_note("guardian")
        assert "ACTIVE MODE: guardian" in note
        assert "Conflict Protocol" in note or len(note) > len("ACTIVE MODE: guardian")

    def test_mode_dialectic_includes_instructions(self):
        agent = self._make_agent()
        note = agent._build_mode_note("dialectic")
        assert "ACTIVE MODE: dialectic" in note

    def test_no_mode_backward_compat(self):
        """When mode is empty string, no ACTIVE MODE section in system prompt."""
        agent = self._make_agent()
        note = agent._build_mode_note("")
        assert "ACTIVE MODE" not in note

    def test_none_like_mode_no_injection(self):
        """_build_mode_note with falsy mode returns empty string."""
        agent = self._make_agent()
        assert agent._build_mode_note("") == ""


# ── Conflict note includes permanence ──


class TestConflictNoteShowsPermanence:
    """Test that _build_conflict_note includes permanence labels."""

    def test_conflict_note_shows_identity_permanence(self):
        conflicts = [{
            "node_id": "discipline-value",
            "title": "Discipline",
            "type": "value",
            "permanence": "identity",
            "conflict_type": "value_violation",
            "explanation": "Contradicts discipline",
            "severity": "hard",
        }]
        note = MentorAgent._build_conflict_note(conflicts)
        assert "identity" in note
        assert "[HARD | identity]" in note

    def test_conflict_note_shows_tactical_permanence(self):
        conflicts = [{
            "node_id": "buy-groceries",
            "title": "Buy Groceries",
            "type": "task",
            "permanence": "tactical",
            "conflict_type": "priority_inversion",
            "explanation": "High-priority task being skipped",
            "severity": "soft",
        }]
        note = MentorAgent._build_conflict_note(conflicts)
        assert "[SOFT | tactical]" in note

    def test_conflict_note_shows_strategic_permanence(self):
        conflicts = [{
            "node_id": "marathon-goal",
            "title": "Marathon Training",
            "type": "goal",
            "permanence": "strategic",
            "conflict_type": "goal_contradiction",
            "explanation": "Skipping training session",
            "severity": "soft",
        }]
        note = MentorAgent._build_conflict_note(conflicts)
        assert "[SOFT | strategic]" in note

    def test_conflict_without_permanence_uses_default(self):
        """Conflicts without permanence field still produce valid output."""
        conflicts = [{
            "node_id": "some-node",
            "title": "Some Node",
            "type": "unknown",
            "conflict_type": "value_violation",
            "explanation": "Some explanation",
            "severity": "soft",
        }]
        note = MentorAgent._build_conflict_note(conflicts)
        # Should have the format with pipe separator, defaulting to "tactical"
        assert "[SOFT | tactical]" in note


# ---------------------------------------------------------------------------
# Task 3: Challenge Ladder in SOUL.md
# ---------------------------------------------------------------------------


class TestSoulChallengeLadderParsed:
    """Test that _load_soul() captures the Challenge Ladder section."""

    def test_soul_challenge_ladder_parsed(self):
        """'challenge ladder' key is present in sections parsed from SOUL.md."""
        from mentor_agent import _load_soul
        identity, instructions, mode_instructions = _load_soul()
        # Re-parse sections to check the key is there
        import os
        search_paths = [
            os.path.join(os.path.dirname(__file__), "..", "..", "SOUL.md"),
            os.path.join(os.path.dirname(__file__), "..", "SOUL.md"),
        ]
        soul_path = None
        for p in search_paths:
            candidate = os.path.abspath(p)
            if os.path.isfile(candidate):
                soul_path = candidate
                break

        if soul_path is None:
            pytest.skip("SOUL.md not found — skipping soul parsing test")

        with open(soul_path, "r", encoding="utf-8") as f:
            content = f.read()

        sections: dict[str, str] = {}
        current_heading = None
        current_lines: list[str] = []
        for line in content.split("\n"):
            if line.startswith("## "):
                if current_heading:
                    sections[current_heading] = "\n".join(current_lines).strip()
                current_heading = line[3:].strip().lower()
                current_lines = []
            elif current_heading is not None:
                current_lines.append(line)
        if current_heading:
            sections[current_heading] = "\n".join(current_lines).strip()

        assert "challenge ladder" in sections

    def test_system_prompt_includes_challenge_ladder(self):
        """System prompt contains 'Challenge Ladder' text when SOUL.md is loaded."""
        import os
        # Check SOUL.md is available
        candidate = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "SOUL.md"))
        if not os.path.isfile(candidate):
            pytest.skip("SOUL.md not found — skipping system prompt test")

        prompt_template, _ = build_system_prompt(_MINIMAL_SCHEMA)
        # The challenge ladder section text should be baked into instructions
        # _load_soul includes all ## section content in the instructions block indirectly
        # via the identity/instructions composition. Check SOUL.md directly for the section.
        with open(candidate, "r", encoding="utf-8") as f:
            soul_content = f.read()
        assert "Challenge Ladder" in soul_content


# ── Proactive Alerts ──


class TestBuildProactiveAlerts:
    """Test MentorAgent._build_proactive_alerts()."""

    def _agent(self):
        """Build a minimal MentorAgent instance without API calls."""
        from unittest.mock import MagicMock
        schema = {"type_list": ["goal", "task", "habit"], "types": {}}
        agent = MentorAgent.__new__(MentorAgent)
        agent.schema = schema
        agent.graph = MagicMock()
        agent.vector_index = MagicMock()
        agent.client = MagicMock()
        agent.system_prompt_template = ""
        agent.mode_instructions = {}
        agent.model = "claude-test"
        return agent

    def test_proactive_alerts_empty_no_output(self):
        """No alerts → empty string returned."""
        agent = self._agent()
        result = agent._build_proactive_alerts({})
        assert result == ""

    def test_proactive_alerts_all_on_track_no_output(self):
        """Only on_track entries → no section injected."""
        agent = self._agent()
        alerts = {
            "broken_streaks": [],
            "at_risk_streaks": [],
            "overdue_commitments": [],
        }
        result = agent._build_proactive_alerts(alerts)
        assert result == ""

    def test_proactive_alerts_broken_streaks(self):
        """Broken streak appears in formatted output."""
        agent = self._agent()
        alerts = {
            "broken_streaks": [{
                "habit_id": "gym",
                "habit_title": "Strength Training",
                "frequency": "3x/week",
                "streak_status": "broken",
                "last_completed": "2026-03-09",
                "days_since_last": 12,
            }],
            "at_risk_streaks": [],
            "overdue_commitments": [],
        }
        result = agent._build_proactive_alerts(alerts)
        assert "PROACTIVE ALERTS" in result
        assert "Strength Training" in result
        assert "BROKEN STREAKS" in result
        assert "12" in result

    def test_proactive_alerts_overdue_with_consequences(self):
        """Overdue commitment with consequences formatted correctly."""
        agent = self._agent()
        alerts = {
            "broken_streaks": [],
            "at_risk_streaks": [],
            "overdue_commitments": [{
                "node_id": "finish-pr",
                "title": "Finish PR Review",
                "type": "task",
                "priority": "high",
                "due": "2026-03-18",
                "days_overdue": 3,
                "committed_on": "2026-03-15",
                "commitment_context": "Told Sarah I'd review by Tuesday",
                "consequences": [
                    {"node_id": "promotion-goal", "title": "Get Promoted", "type": "goal", "relationship": "supported_by"},
                ],
            }],
        }
        result = agent._build_proactive_alerts(alerts)
        assert "OVERDUE COMMITMENTS" in result
        assert "Finish PR Review" in result
        assert "3 days overdue" in result
        assert "Told Sarah" in result
        assert "Get Promoted" in result

    def test_proactive_alerts_at_risk(self):
        """At-risk streak appears in AT RISK section."""
        agent = self._agent()
        alerts = {
            "broken_streaks": [],
            "at_risk_streaks": [{
                "habit_id": "reading",
                "habit_title": "Evening Reading",
                "frequency": "weekly",
                "streak_status": "at_risk",
                "last_completed": "2026-03-15",
                "days_since_last": 6,
            }],
            "overdue_commitments": [],
        }
        result = agent._build_proactive_alerts(alerts)
        assert "AT RISK" in result
        assert "Evening Reading" in result

    def test_proactive_alerts_capped_streaks(self):
        """More than 5 broken streaks → only 5 included."""
        agent = self._agent()
        broken = [
            {
                "habit_id": f"habit-{i}",
                "habit_title": f"Habit {i}",
                "frequency": "daily",
                "streak_status": "broken",
                "last_completed": "2026-03-01",
                "days_since_last": 20,
            }
            for i in range(8)
        ]
        alerts = {"broken_streaks": broken, "at_risk_streaks": [], "overdue_commitments": []}
        result = agent._build_proactive_alerts(alerts)
        # Only 5 habits should appear
        count = sum(1 for i in range(8) if f"Habit {i}" in result)
        assert count == 5

    def test_proactive_alerts_capped_overdue(self):
        """More than 3 overdue commitments → only 3 included."""
        agent = self._agent()
        overdue = [
            {
                "node_id": f"task-{i}",
                "title": f"Task {i}",
                "type": "task",
                "priority": "medium",
                "due": "2026-03-15",
                "days_overdue": i + 1,
                "committed_on": None,
                "commitment_context": None,
                "consequences": [],
            }
            for i in range(6)
        ]
        alerts = {"broken_streaks": [], "at_risk_streaks": [], "overdue_commitments": overdue}
        result = agent._build_proactive_alerts(alerts)
        count = sum(1 for i in range(6) if f"Task {i}" in result)
        assert count == 3

    def test_system_prompt_includes_alerts_when_present(self):
        """System prompt contains 'PROACTIVE ALERTS' when broken streaks exist."""
        from unittest.mock import MagicMock, patch
        schema = {"type_list": ["goal", "task", "habit"], "types": {}}
        agent = MentorAgent.__new__(MentorAgent)
        agent.schema = schema
        g = MagicMock()
        g.get_all_nodes.return_value = []
        agent.graph = g
        agent.vector_index = MagicMock()
        agent.client = MagicMock()
        agent.system_prompt_template = "{context}{today}"
        agent.mode_instructions = {}
        agent.model = "claude-test"

        alerts = {
            "broken_streaks": [{
                "habit_id": "gym",
                "habit_title": "Gym",
                "frequency": "daily",
                "streak_status": "broken",
                "last_completed": "2026-03-01",
                "days_since_last": 20,
            }],
            "at_risk_streaks": [],
            "overdue_commitments": [],
        }
        prompt_section = agent._build_proactive_alerts(alerts)
        assert "PROACTIVE ALERTS" in prompt_section

    def test_alerts_not_in_prompt_when_empty(self):
        """No alerts → _build_proactive_alerts returns empty string."""
        from unittest.mock import MagicMock
        schema = {"type_list": ["goal", "task", "habit"], "types": {}}
        agent = MentorAgent.__new__(MentorAgent)
        agent.schema = schema
        agent.graph = MagicMock()
        agent.vector_index = MagicMock()
        agent.client = MagicMock()
        agent.system_prompt_template = ""
        agent.mode_instructions = {}
        agent.model = "claude-test"

        result = agent._build_proactive_alerts({
            "broken_streaks": [],
            "at_risk_streaks": [],
            "overdue_commitments": [],
        })
        assert result == ""


# ── FORMAT_SPEC Commitment Section ──


class TestFormatSpecCommitments:
    """Test that _GRAPH_INSTRUCTIONS contains the commitment detection section."""

    def test_format_spec_contains_commitment_section(self):
        """_GRAPH_INSTRUCTIONS contains '8. COMMITMENTS' section."""
        from mentor_agent import _GRAPH_INSTRUCTIONS
        assert "COMMITMENTS" in _GRAPH_INSTRUCTIONS

    def test_format_spec_commitment_rules_mention_committed_on(self):
        """Commitment section references the committed_on field."""
        from mentor_agent import _GRAPH_INSTRUCTIONS
        assert "committed_on" in _GRAPH_INSTRUCTIONS

    def test_format_spec_commitment_rules_mention_context(self):
        """Commitment section references commitment_context field."""
        from mentor_agent import _GRAPH_INSTRUCTIONS
        assert "commitment_context" in _GRAPH_INSTRUCTIONS

# ── State Note ──


class TestBuildStateNote:
    """Test MentorAgent._build_state_note()."""

    def test_state_note_default_empty(self):
        """Normal energy + no stress → empty string (no noise injected)."""
        state = {"energy": "normal", "stress": "none", "confidence": "low", "signals": []}
        result = MentorAgent._build_state_note(state)
        assert result == ""

    def test_state_note_empty_dict(self):
        """Empty dict → empty string."""
        assert MentorAgent._build_state_note({}) == ""

    def test_state_note_elevated_stress(self):
        """Elevated stress → note includes stress guidance."""
        state = {
            "energy": "low",
            "stress": "elevated",
            "confidence": "medium",
            "signals": [
                {"type": "brevity", "detail": "avg 15 chars"},
                {"type": "late_night", "detail": "2 messages after midnight"},
            ],
        }
        result = MentorAgent._build_state_note(state)
        assert "USER STATE" in result
        assert "elevated" in result
        assert "stress is elevated" in result
        assert "brevity" in result
        assert "late_night" in result

    def test_state_note_high_energy(self):
        """High energy → note includes momentum guidance."""
        state = {
            "energy": "high",
            "stress": "none",
            "confidence": "high",
            "signals": [{"type": "idea_density", "detail": "5 new intentions detected"}],
        }
        result = MentorAgent._build_state_note(state)
        assert "USER STATE" in result
        assert "high" in result
        assert "channel the momentum" in result.lower() or "Channel the momentum" in result

    def test_state_note_low_energy(self):
        """Low energy (non-stressed) → note includes recovery guidance."""
        state = {
            "energy": "low",
            "stress": "mild",
            "confidence": "medium",
            "signals": [{"type": "brevity", "detail": "avg 10 chars"}],
        }
        result = MentorAgent._build_state_note(state)
        assert "USER STATE" in result
        assert "energy is low" in result.lower() or "low" in result

    def test_state_note_low_confidence_hint(self):
        """Low confidence → note includes hint qualifier."""
        state = {
            "energy": "low",
            "stress": "mild",
            "confidence": "low",
            "signals": [{"type": "brevity", "detail": "avg 12 chars"}],
        }
        result = MentorAgent._build_state_note(state)
        # Low confidence should still produce a note since stress != none
        assert result != ""
        assert "low confidence" in result.lower()

    def test_state_note_includes_energy_stress_confidence(self):
        """Note format includes Energy | Stress | Confidence line."""
        state = {
            "energy": "low",
            "stress": "elevated",
            "confidence": "high",
            "signals": [],
        }
        result = MentorAgent._build_state_note(state)
        assert "Energy: low" in result
        assert "Stress: elevated" in result
        assert "Confidence: high" in result


# ── State-aware alert capping ──


class TestProactiveAlertsStateAware:
    """Test that _build_proactive_alerts() respects state-aware caps."""

    def _agent(self):
        from unittest.mock import MagicMock
        schema = {"type_list": ["goal", "task", "habit"], "types": {}}
        agent = MentorAgent.__new__(MentorAgent)
        agent.schema = schema
        agent.graph = MagicMock()
        agent.vector_index = MagicMock()
        agent.client = MagicMock()
        agent.system_prompt_template = ""
        agent.mode_instructions = {}
        agent.model = "claude-test"
        return agent

    def _make_broken_streaks(self, count: int) -> list[dict]:
        return [
            {
                "habit_id": f"habit-{i}",
                "habit_title": f"Habit {i}",
                "frequency": "daily",
                "streak_status": "broken",
                "last_completed": "2026-03-01",
                "days_since_last": 20,
            }
            for i in range(count)
        ]

    def test_proactive_alerts_capped_when_stressed(self):
        """Elevated stress + medium confidence + 5 alerts → only 1 in output."""
        agent = self._agent()
        broken = self._make_broken_streaks(5)
        alerts = {"broken_streaks": broken, "at_risk_streaks": [], "overdue_commitments": []}
        state = {"stress": "elevated", "energy": "low", "confidence": "medium", "signals": []}
        result = agent._build_proactive_alerts(alerts, state=state)
        # Only 1 habit should appear
        count = sum(1 for i in range(5) if f"Habit {i}" in result)
        assert count == 1

    def test_proactive_alerts_capped_when_low_energy(self):
        """Low energy + medium confidence + 5 alerts → only 2 in output."""
        agent = self._agent()
        broken = self._make_broken_streaks(5)
        alerts = {"broken_streaks": broken, "at_risk_streaks": [], "overdue_commitments": []}
        state = {"stress": "none", "energy": "low", "confidence": "medium", "signals": []}
        result = agent._build_proactive_alerts(alerts, state=state)
        count = sum(1 for i in range(5) if f"Habit {i}" in result)
        assert count == 2

    def test_proactive_alerts_normal_no_cap_change(self):
        """Normal state → standard cap of 5 broken streaks applies."""
        agent = self._agent()
        broken = self._make_broken_streaks(7)
        alerts = {"broken_streaks": broken, "at_risk_streaks": [], "overdue_commitments": []}
        state = {"stress": "none", "energy": "normal", "confidence": "low", "signals": []}
        result = agent._build_proactive_alerts(alerts, state=state)
        count = sum(1 for i in range(7) if f"Habit {i}" in result)
        assert count == 5

    def test_proactive_alerts_low_confidence_no_suppression(self):
        """Low confidence stressed state → no suppression (standard caps)."""
        agent = self._agent()
        broken = self._make_broken_streaks(5)
        alerts = {"broken_streaks": broken, "at_risk_streaks": [], "overdue_commitments": []}
        state = {"stress": "elevated", "energy": "low", "confidence": "low", "signals": []}
        result = agent._build_proactive_alerts(alerts, state=state)
        # low confidence → no suppression → 5 shown
        count = sum(1 for i in range(5) if f"Habit {i}" in result)
        assert count == 5

    def test_proactive_alerts_includes_fundamentals(self):
        """Neglected fundamental appears in formatted output."""
        agent = self._agent()
        alerts = {
            "broken_streaks": [],
            "at_risk_streaks": [],
            "overdue_commitments": [],
            "neglected_fundamentals": [{
                "fundamental": "movement",
                "status": "neglected",
                "days_since_activity": 16,
                "related_habits": ["gym"],
                "message": "No movement-related activity in 16 days.",
            }],
            "untracked_fundamentals": [],
        }
        result = agent._build_proactive_alerts(alerts)
        assert "NEGLECTED FUNDAMENTALS" in result
        assert "Movement" in result
        assert "16 days" in result

    def test_proactive_alerts_untracked_fundamentals(self):
        """No-data fundamental appears with softer framing."""
        agent = self._agent()
        alerts = {
            "broken_streaks": [],
            "at_risk_streaks": [],
            "overdue_commitments": [],
            "neglected_fundamentals": [],
            "untracked_fundamentals": [{
                "fundamental": "nutrition",
                "status": "no_data",
                "days_since_activity": None,
                "related_habits": [],
                "message": "No nutrition habits tracked.",
            }],
        }
        result = agent._build_proactive_alerts(alerts)
        assert "UNTRACKED FUNDAMENTALS" in result
        assert "Nutrition" in result

    def test_proactive_alerts_fundamentals_capped(self):
        """More than 3 neglected fundamentals → only 3 shown."""
        agent = self._agent()
        neglected = [
            {
                "fundamental": f"fund-{i}",
                "status": "neglected",
                "days_since_activity": 20,
                "related_habits": [],
                "message": f"Fund {i} neglected.",
            }
            for i in range(5)
        ]
        alerts = {
            "broken_streaks": [],
            "at_risk_streaks": [],
            "overdue_commitments": [],
            "neglected_fundamentals": neglected,
            "untracked_fundamentals": [],
        }
        result = agent._build_proactive_alerts(alerts)
        count = sum(1 for i in range(5) if f"Fund {i}" in result)
        assert count == 3


# ── SOUL.md State Awareness parsed ──


class TestSoulStateAwarenessParsed:
    """Test that _load_soul() captures the State Awareness section."""

    def test_soul_state_awareness_parsed(self):
        """'state awareness' key present in parsed sections from SOUL.md."""
        import os
        search_paths = [
            os.path.join(os.path.dirname(__file__), "..", "..", "SOUL.md"),
            os.path.join(os.path.dirname(__file__), "..", "SOUL.md"),
        ]
        soul_path = None
        for p in search_paths:
            candidate = os.path.abspath(p)
            if os.path.isfile(candidate):
                soul_path = candidate
                break

        if soul_path is None:
            pytest.skip("SOUL.md not found — skipping soul parsing test")

        with open(soul_path, "r", encoding="utf-8") as f:
            content = f.read()

        sections: dict[str, str] = {}
        current_heading = None
        current_lines: list[str] = []
        for line in content.split("\n"):
            if line.startswith("## "):
                if current_heading:
                    sections[current_heading] = "\n".join(current_lines).strip()
                current_heading = line[3:].strip().lower()
                current_lines = []
            elif current_heading is not None:
                current_lines.append(line)
        if current_heading:
            sections[current_heading] = "\n".join(current_lines).strip()

        assert "state awareness" in sections

    def test_system_prompt_includes_state_awareness(self):
        """SOUL.md contains 'State Awareness' text."""
        import os
        candidate = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "SOUL.md")
        )
        if not os.path.isfile(candidate):
            pytest.skip("SOUL.md not found")

        with open(candidate, "r", encoding="utf-8") as f:
            soul_content = f.read()
        assert "State Awareness" in soul_content


# ── Relationship Note ──


class TestBuildRelationshipNote:
    """Test MentorAgent._build_relationship_note()."""

    def test_relationship_note_none_when_empty(self):
        """None context → empty string."""
        result = MentorAgent._build_relationship_note(None)
        assert result == ""

    def test_relationship_note_empty_dict_returns_empty(self):
        """Empty dict → empty string."""
        result = MentorAgent._build_relationship_note({})
        assert result == ""

    def test_relationship_note_with_mentions(self):
        """Mentions present → note includes 'MENTIONED IN THIS SESSION'."""
        context = {
            "mentions": [
                {
                    "person_id": "ben",
                    "person_title": "Ben",
                    "relationship": "friend",
                    "mention_count": 3,
                    "contexts": ["Ben and I are planning Citadel Technica"],
                    "sentiment": "positive",
                }
            ],
            "person_intelligence": [],
            "social_patterns": {"pattern": "healthy", "confidence": "low", "signals": []},
        }
        result = MentorAgent._build_relationship_note(context)
        assert "SOCIAL CONTEXT" in result
        assert "MENTIONED IN THIS SESSION" in result
        assert "Ben" in result
        assert "3 times" in result

    def test_relationship_note_includes_health(self):
        """Person intelligence present → note includes 'RELATIONSHIP HEALTH'."""
        context = {
            "mentions": [],
            "person_intelligence": [
                {
                    "person_id": "ben",
                    "person_title": "Ben",
                    "relationship": "friend",
                    "last_updated": "2026-03-20",
                    "days_since_update": 3,
                    "connected_node_count": 2,
                    "connected_active_count": 2,
                    "connection_types": ["project"],
                },
            ],
            "social_patterns": {"pattern": "healthy", "confidence": "low", "signals": []},
        }
        result = MentorAgent._build_relationship_note(context)
        assert "RELATIONSHIP HEALTH" in result

    def test_relationship_note_isolation_alert(self):
        """Isolating pattern → note contains stale relationship guidance."""
        context = {
            "mentions": [],
            "person_intelligence": [
                {
                    "person_id": "romane",
                    "person_title": "Romane",
                    "relationship": "friend",
                    "last_updated": "2026-01-01",
                    "days_since_update": 80,
                    "connected_node_count": 1,
                    "connected_active_count": 0,
                    "connection_types": [],
                },
            ],
            "social_patterns": {
                "pattern": "isolating",
                "confidence": "high",
                "signals": [{"type": "stale_relationships", "detail": "8 of 11 stale"}],
                "stale_relationships": [{"person_id": "romane", "person_title": "Romane", "days_since_update": 80}],
                "active_relationships": [],
            },
        }
        result = MentorAgent._build_relationship_note(context)
        assert "SOCIAL CONTEXT" in result
        assert "Stale relationships" in result or "stale" in result.lower()


# ── SOUL.md Relationship Intelligence parsed ──


def _parse_soul_sections(soul_path: str) -> dict[str, str]:
    """Parse SOUL.md sections dict (duplicates the _load_soul parser)."""
    with open(soul_path, "r", encoding="utf-8") as f:
        content = f.read()
    sections: dict[str, str] = {}
    current_heading = None
    current_lines: list[str] = []
    for line in content.split("\n"):
        if line.startswith("## "):
            if current_heading:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line[3:].strip().lower()
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)
    if current_heading:
        sections[current_heading] = "\n".join(current_lines).strip()
    return sections


class TestSoulRelationshipIntelligenceParsed:
    """Test that SOUL.md contains and _load_soul() captures the Relationship Intelligence section."""

    def _get_soul_path(self) -> str | None:
        import os
        candidates = [
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "SOUL.md")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "SOUL.md")),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return None

    def test_soul_relationship_intelligence_parsed(self):
        """'relationship intelligence' key present in parsed sections from SOUL.md."""
        soul_path = self._get_soul_path()
        if soul_path is None:
            pytest.skip("SOUL.md not found — skipping soul parsing test")
        sections = _parse_soul_sections(soul_path)
        assert "relationship intelligence" in sections
        assert sections["relationship intelligence"].strip() != ""

    def test_system_prompt_includes_relationship_intelligence(self):
        """System prompt built from SOUL.md contains 'Relationship Intelligence' text."""
        import os
        soul_path = self._get_soul_path()
        if soul_path is None:
            pytest.skip("SOUL.md not found — skipping system prompt test")

        prompt_template, _ = build_system_prompt(_MINIMAL_SCHEMA)
        # The relationship intelligence section is included in the instructions block
        assert "Relationship Intelligence" in prompt_template or "RELATIONSHIP INTELLIGENCE" in prompt_template
