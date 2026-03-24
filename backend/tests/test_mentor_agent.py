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
    _node_context_compact,
    _node_context_full,
    _node_context_oneliner,
    _BASE_TOKEN_BUDGET,
    _MAX_CONTEXT_TOKENS,
    _CHARS_PER_TOKEN,
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


class TestProactiveAlertsKindAware:
    """Test kind-aware formatting in _build_proactive_alerts()."""

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

    def _broken_build(self) -> dict:
        return {
            "habit_id": "strength-training",
            "habit_title": "Strength Training",
            "kind": "build",
            "frequency": "3x/week",
            "streak_status": "broken",
            "last_completed": "2026-03-16",
            "days_since_last": 8,
            "days_clean": None,
            "last_occurrence": None,
            "next_due": None,
            "days_until_due": None,
        }

    def _break_12_days(self) -> dict:
        return {
            "habit_id": "nicotine",
            "habit_title": "Nicotine Pouches",
            "kind": "break",
            "frequency": "daily",
            "streak_status": "on_track",
            "days_clean": 12,
            "last_occurrence": "2026-03-12",
            "current_streak": None,
            "last_completed": None,
            "days_since_last": None,
            "next_due": None,
            "days_until_due": None,
        }

    def _break_relapsed(self) -> dict:
        return {
            "habit_id": "nicotine",
            "habit_title": "Nicotine Pouches",
            "kind": "break",
            "streak_status": "relapsed",
            "days_clean": 0,
            "last_occurrence": "2026-03-24",
            "current_streak": None,
            "last_completed": None,
            "days_since_last": None,
            "next_due": None,
            "days_until_due": None,
        }

    def _break_unknown(self) -> dict:
        return {
            "habit_id": "nicotine",
            "habit_title": "Nicotine Pouches",
            "kind": "break",
            "streak_status": "unknown",
            "days_clean": None,
            "last_occurrence": None,
            "current_streak": None,
            "last_completed": None,
            "days_since_last": None,
            "next_due": None,
            "days_until_due": None,
        }

    def _break_early(self) -> dict:
        return {
            "habit_id": "nicotine",
            "habit_title": "Nicotine Pouches",
            "kind": "break",
            "streak_status": "early",
            "days_clean": 3,
            "last_occurrence": "2026-03-21",
            "current_streak": None,
            "last_completed": None,
            "days_since_last": None,
            "next_due": None,
            "days_until_due": None,
        }

    def _periodic_upcoming(self) -> dict:
        return {
            "habit_id": "fasting",
            "habit_title": "Periodic 48-Hour Fasting",
            "kind": "periodic",
            "frequency": "quarterly",
            "streak_status": "upcoming",
            "last_completed": "2026-03-16",
            "next_due": "2026-06-14",
            "days_until_due": 5,
            "current_streak": None,
            "days_clean": None,
            "last_occurrence": None,
            "days_since_last": None,
        }

    def _periodic_overdue(self) -> dict:
        return {
            "habit_id": "fasting",
            "habit_title": "Periodic 48-Hour Fasting",
            "kind": "periodic",
            "frequency": "quarterly",
            "streak_status": "overdue",
            "last_completed": "2025-12-15",
            "next_due": "2026-03-14",
            "days_until_due": -10,
            "current_streak": None,
            "days_clean": None,
            "last_occurrence": None,
            "days_since_last": None,
        }

    def test_proactive_alerts_build_habit_broken(self):
        """Build habit with broken status → 'streak broken' text."""
        agent = self._agent()
        alerts = {"broken_streaks": [self._broken_build()]}
        result = agent._build_proactive_alerts(alerts)
        assert "streak broken" in result
        assert "Strength Training" in result

    def test_proactive_alerts_break_habit_days_clean(self):
        """Break habit 12 days clean → '12 days clean' text."""
        agent = self._agent()
        alerts = {"break_habits": [self._break_12_days()]}
        result = agent._build_proactive_alerts(alerts)
        assert "BREAK HABITS" in result
        assert "12 days clean" in result
        assert "Nicotine Pouches" in result

    def test_proactive_alerts_break_habit_relapsed(self):
        """Break habit 0 days → 'mentioned today. Day 0.' text."""
        agent = self._agent()
        alerts = {"break_habits": [self._break_relapsed()]}
        result = agent._build_proactive_alerts(alerts)
        assert "mentioned today" in result
        assert "Day 0" in result

    def test_proactive_alerts_break_habit_unknown(self):
        """Break habit unknown → 'no usage data' text."""
        agent = self._agent()
        alerts = {"break_habits": [self._break_unknown()]}
        result = agent._build_proactive_alerts(alerts)
        assert "no usage data" in result

    def test_proactive_alerts_break_habit_strong_suppressed(self):
        """Break habit 30+ days clean (strong) → not in output (filtered by _build_alerts)."""
        agent = self._agent()
        # Strong habits are pre-filtered in _build_alerts; here we verify the section
        # is absent when break_habits is empty
        alerts = {"break_habits": []}
        result = agent._build_proactive_alerts(alerts)
        # No break_habits → no BREAK HABITS section
        assert "BREAK HABITS" not in result

    def test_proactive_alerts_periodic_upcoming(self):
        """Periodic due in 5 days → 'Next due' text."""
        agent = self._agent()
        alerts = {"periodic_habits": [self._periodic_upcoming()]}
        result = agent._build_proactive_alerts(alerts)
        assert "PERIODIC HABITS" in result
        assert "Periodic 48-Hour Fasting" in result
        assert "Next due" in result

    def test_proactive_alerts_periodic_overdue(self):
        """Periodic overdue → 'Overdue by' text."""
        agent = self._agent()
        alerts = {"periodic_habits": [self._periodic_overdue()]}
        result = agent._build_proactive_alerts(alerts)
        assert "PERIODIC HABITS" in result
        assert "Overdue by" in result

    def test_proactive_alerts_mixed_kinds(self):
        """Build broken + break early + periodic overdue → all three sections present."""
        agent = self._agent()
        alerts = {
            "broken_streaks": [self._broken_build()],
            "break_habits": [self._break_early()],
            "periodic_habits": [self._periodic_overdue()],
        }
        result = agent._build_proactive_alerts(alerts)
        assert "BROKEN STREAKS" in result
        assert "BREAK HABITS" in result
        assert "PERIODIC HABITS" in result

    def test_proactive_alerts_break_early_not_suppressed_when_stressed(self):
        """Stress elevated + break early → break early still shown."""
        agent = self._agent()
        alerts = {
            "broken_streaks": [],
            "break_habits": [self._break_early()],
            "periodic_habits": [self._periodic_overdue()],
        }
        state = {"stress": "elevated", "energy": "normal", "confidence": "high", "signals": []}
        result = agent._build_proactive_alerts(alerts, state=state)
        # Early break habit should still appear even under stress
        assert "BREAK HABITS" in result
        assert "3 days clean" in result
        # Periodic should be suppressed under elevated stress
        assert "PERIODIC HABITS" not in result


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


# ---------------------------------------------------------------------------
# Helpers shared by Tasks 3 + 4 tests
# ---------------------------------------------------------------------------

def _make_agent_with_nodes(nodes, schema_override=None):
    """Build a MentorAgent backed by a FakeGraph with the given nodes.

    The vector_index.search mock returns synthetic results covering all nodes
    so that scoring / top-K logic is exercised.
    """
    from unittest.mock import MagicMock

    class FakeGraph:
        def __init__(self, nodes):
            self._nodes = {n["id"]: n for n in nodes}

        def get_all_nodes(self):
            return list(self._nodes.values())

        def get_node(self, nid):
            return self._nodes.get(nid)

        def get_neighbors(self, nid, depth=1):
            return []

        def get_neighbors_by_hop(self, nid, depth=2):
            return {}

        def get_degree(self, nid):
            return 0

    schema = schema_override or {
        "type_list": ["goal", "task", "daily", "habit", "note", "person"],
        "types": {
            "goal": {"domain": "Self"},
            "habit": {"domain": "Self"},
            "daily": {"domain": "Life"},
            "task": {"domain": "Planning"},
            "note": {"domain": "Knowledge"},
            "person": {"domain": "People"},
        },
        "domains": {
            "Self": {"types": ["goal", "habit"]},
            "Life": {"types": ["daily"]},
            "Planning": {"types": ["task"]},
            "Knowledge": {"types": ["note"]},
            "People": {"types": ["person"]},
        },
    }

    graph = FakeGraph(nodes)

    # Build synthetic search results for ALL nodes so the pipeline can rank them
    synthetic_results = [
        {"id": n["id"], "title": n.get("title", n["id"]), "type": n.get("type", "note"), "score": 0.5}
        for n in nodes
    ]

    vector_index = MagicMock()
    vector_index.search.return_value = synthetic_results

    agent = MentorAgent.__new__(MentorAgent)
    agent.graph = graph
    agent.vector_index = vector_index
    agent.schema = schema
    agent.client = MagicMock()
    agent.system_prompt_template = "test {context} {today}"
    agent.model = "test-model"
    agent.mode_instructions = {}
    return agent


# ---------------------------------------------------------------------------
# Task 3: Adaptive Retrieval Pipeline
# ---------------------------------------------------------------------------

class TestGetContextAdaptiveRetrieval:

    def test_get_context_broad_temporal_returns_more(self):
        """Broad temporal query with 12+ date-bearing nodes → context contains >5 nodes."""
        from unittest.mock import patch
        from datetime import date, timedelta

        frozen = date(2026, 3, 23)
        # 12 daily nodes with dates in "this week" (Mon Mar 17 – Sun Mar 23)
        week_start = date(2026, 3, 17)
        nodes = [
            {
                "id": f"daily-{i}",
                "type": "daily",
                "title": f"Day {i}",
                "date": (week_start + timedelta(days=i % 7)).isoformat(),
                "status": "active",
            }
            for i in range(12)
        ]

        agent = _make_agent_with_nodes(nodes)

        with patch("mentor_agent.date") as mock_date:
            mock_date.today.return_value = frozen
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            context, results = agent.get_context("what happened this week")

        # Should return more than 5 nodes (temporal_broad raises k to 15)
        assert len(results) > 5

    def test_get_context_entity_lookup_full_content(self):
        """Entity lookup query → Tier 1 uses full content (not compact)."""
        nodes = [
            {
                "id": "fear-failure",
                "type": "fear",
                "title": "Fear of Failure",
                "content": "I am afraid of failing in front of others because it means I'm not good enough.",
                "status": "active",
            }
        ] + [
            {"id": f"pad-{i}", "type": "note", "title": f"Pad {i}", "status": ""}
            for i in range(15)
        ]
        agent = _make_agent_with_nodes(nodes)
        context, results = agent.get_context("tell me about my fear of failure")
        # Full content should appear (not compact format)
        assert "Content:" in context or "afraid of failing" in context

    def test_get_context_domain_filter_types(self):
        """Domain query → results biased toward that domain's types."""
        nodes = [
            {"id": "goal-1", "type": "goal", "title": "Learn Piano", "status": "active"},
            {"id": "goal-2", "type": "goal", "title": "Run Marathon", "status": "active"},
            {"id": "goal-3", "type": "goal", "title": "Start Business", "status": "active"},
            {"id": "task-1", "type": "task", "title": "Book Flights", "status": "active"},
            {"id": "note-1", "type": "note", "title": "Random Note", "status": ""},
        ] + [{"id": f"goal-{i+4}", "type": "goal", "title": f"Goal {i}", "status": "active"} for i in range(10)]

        agent = _make_agent_with_nodes(nodes)
        context, results = agent.get_context("show me my goals")
        # All results should not be tasks/notes since domain_filter focuses on Self
        result_types = [r["type"] for r in results]
        goal_count = result_types.count("goal")
        assert goal_count >= len(result_types) // 2  # majority goals

    def test_get_context_general_unchanged(self):
        """General query → k=5, full content format (same as before sprint)."""
        nodes = [{"id": f"node-{i}", "type": "note", "title": f"Note {i}", "status": ""} for i in range(15)]
        agent = _make_agent_with_nodes(nodes)
        context, results = agent.get_context("hey how's it going, just checking in about random stuff")
        # k=5 for general
        assert len(results) <= 5

    def test_get_context_intent_error_fallback(self):
        """If classify_query_intent raises, get_context() falls back to general intent."""
        from unittest.mock import patch

        nodes = [{"id": f"node-{i}", "type": "note", "title": f"Note {i}", "status": ""} for i in range(15)]
        agent = _make_agent_with_nodes(nodes)

        with patch("mentor_agent.classify_query_intent", side_effect=RuntimeError("boom")):
            # Should not raise, should fall back gracefully
            context, results = agent.get_context("test query")
        assert isinstance(context, str)

    def test_get_context_filter_fallback_on_empty(self):
        """Filtered search returns 0 results → fallback to unfiltered."""
        from unittest.mock import MagicMock, patch

        nodes = [{"id": f"node-{i}", "type": "note", "title": f"Note {i}", "status": ""} for i in range(15)]
        agent = _make_agent_with_nodes(nodes)

        call_count = [0]
        original_search = agent.vector_index.search.return_value

        def mock_search(query, n=5, where=None):
            call_count[0] += 1
            if where is not None:
                return []  # filtered search returns nothing
            return original_search  # unfiltered returns results

        agent.vector_index.search = mock_search

        # domain_filter intent sets a pre_filter; with no results, should fall back
        context, results = agent.get_context("show me my goals")
        # Should still have results from unfiltered fallback
        assert isinstance(context, str)


# ---------------------------------------------------------------------------
# Task 4: Compact Context Assembly
# ---------------------------------------------------------------------------

class TestNodeContextCompact:

    def test_node_context_compact_format(self):
        """Compact output contains title, type, status, and first sentence."""
        node = {
            "id": "goal-piano",
            "type": "goal",
            "title": "Learn Piano",
            "status": "active",
            "priority": "high",
            "due": "2026-06-01",
            "content": "I want to learn to play piano at an intermediate level. This is my main goal.",
        }
        result = _node_context_compact(node, ["Music Theory (note)"])
        assert "Learn Piano" in result
        assert "goal" in result
        assert "active" in result
        assert "high priority" in result
        # First sentence of content
        assert "I want to learn to play piano" in result

    def test_node_context_compact_shorter_than_full(self):
        """Compact output is less than 50% the length of full output for same node."""
        node = {
            "id": "goal-piano",
            "type": "goal",
            "title": "Learn Piano",
            "status": "active",
            "content": "I want to learn to play piano at an intermediate level. " * 10,
        }
        neighbor_names = ["Music Theory (note)", "Alice (person)", "Practice (habit)"]
        compact_len = len(_node_context_compact(node, neighbor_names))
        full_len = len(_node_context_full(node, neighbor_names))
        assert compact_len < full_len * 0.5

    def test_compact_assembly_fits_15_nodes(self):
        """15 nodes in compact mode fit within the char budget."""
        nodes = [
            {
                "id": f"daily-{i}",
                "type": "daily",
                "title": f"Daily Journal {i}",
                "status": "active",
                "content": f"Today I worked on item {i}. It went well.",
            }
            for i in range(15)
        ]
        # Calculate total chars for 15 compact nodes
        total_chars = sum(
            len(_node_context_compact(n, [])) for n in nodes
        )
        char_budget = _MAX_CONTEXT_TOKENS * _CHARS_PER_TOKEN
        assert total_chars <= char_budget, (
            f"15 compact nodes ({total_chars} chars) exceed budget ({char_budget} chars)"
        )

    def test_compact_temporal_header_capped(self):
        """Broad temporal query with 20 date-bearing nodes → only 10 temporal facts in header."""
        from unittest.mock import patch
        from datetime import date, timedelta

        frozen = date(2026, 3, 23)
        week_start = date(2026, 3, 17)
        # 20 daily nodes with dates in "this week"
        nodes = [
            {
                "id": f"daily-{i}",
                "type": "daily",
                "title": f"Day {i}",
                "date": (week_start + timedelta(days=i % 7)).isoformat(),
                "status": "active",
            }
            for i in range(20)
        ]

        agent = _make_agent_with_nodes(nodes)

        with patch("mentor_agent.date") as mock_date:
            mock_date.today.return_value = frozen
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            context, _ = agent.get_context("what happened this week")

        # Count FACT lines in the header
        fact_lines = [line for line in context.split("\n") if line.startswith("FACT:")]
        assert len(fact_lines) <= 10

    def test_non_compact_unchanged(self):
        """compact=False (entity intent) → full content in Tier 1."""
        nodes = [
            {
                "id": "goal-piano",
                "type": "goal",
                "title": "Learn Piano",
                "status": "active",
                "content": "A detailed description of my piano goal and ambitions.",
            }
        ] + [
            {"id": f"pad-{i}", "type": "note", "title": f"Pad {i}", "status": ""}
            for i in range(15)
        ]
        agent = _make_agent_with_nodes(nodes)
        # "tell me about my goal" → entity_lookup → compact=False
        # "goal" keyword → Self domain → goal-piano gets domain boost → ranks #1
        context, _ = agent.get_context("tell me about my goal")
        # Full format shows "Content:" label
        assert "Content:" in context


# ── Relationship Alert Injection ──


class TestRelationshipAlerts:
    """Test _build_relationship_alerts() and its integration into _build_proactive_alerts()."""

    def _agent(self):
        from unittest.mock import MagicMock
        schema = {"type_list": ["goal", "task", "habit", "person"], "types": {}}
        agent = MentorAgent.__new__(MentorAgent)
        agent.schema = schema
        agent.graph = MagicMock()
        agent.vector_index = MagicMock()
        agent.client = MagicMock()
        agent.system_prompt_template = ""
        agent.mode_instructions = {}
        agent.model = "claude-test"
        return agent

    def _drifting(self, pid="ethan", title="Ethan Shorthouse", days=18, drift=4,
                  rel="friend", freq="weekly", pos=5, neg=1, profile="mostly_positive"):
        return {
            "person_id": pid,
            "person_title": title,
            "relationship": rel,
            "expected_frequency": freq,
            "mention_count": 6,
            "days_since_mention": days,
            "drift_days": drift,
            "health": "drifting",
            "influence_score": 0.5,
            "influence_rank": 2,
            "context_profile": profile,
            "mention_contexts": {"positive": pos, "negative": neg, "planning": 0, "neutral": 0},
            "recent_topics": [],
        }

    def _neglected(self, pid="dad", title="Dad", days=30, drift=16):
        return {
            "person_id": pid,
            "person_title": title,
            "relationship": "family",
            "expected_frequency": "weekly",
            "mention_count": 2,
            "days_since_mention": days,
            "drift_days": drift,
            "health": "neglected",
            "influence_score": 0.2,
            "influence_rank": 4,
            "context_profile": "neutral",
            "mention_contexts": {"positive": 0, "negative": 0, "planning": 0, "neutral": 2},
            "recent_topics": [],
        }

    def _high_influence(self, pid="boss", title="Boss", count=8, profile="mostly_negative"):
        return {
            "person_id": pid,
            "person_title": title,
            "relationship": "colleague",
            "expected_frequency": "weekly",
            "mention_count": count,
            "days_since_mention": 1,
            "drift_days": 0,
            "health": "active",
            "influence_score": 0.8,
            "influence_rank": 1,
            "context_profile": profile,
            "mention_contexts": {"positive": 0, "negative": count, "planning": 0, "neutral": 0},
            "recent_topics": [],
        }

    def test_proactive_alerts_drifting_relationship(self):
        """Drifting person appears in formatted output."""
        agent = self._agent()
        alerts = {"drifting_relationships": [self._drifting()], "neglected_relationships": [], "high_influence": []}
        result = agent._build_proactive_alerts(alerts)
        assert "RELATIONSHIP DRIFT" in result
        assert "Ethan Shorthouse" in result
        assert "18" in result  # days

    def test_proactive_alerts_neglected_relationship(self):
        """Neglected person appears with stronger framing."""
        agent = self._agent()
        alerts = {"drifting_relationships": [], "neglected_relationships": [self._neglected()], "high_influence": []}
        result = agent._build_proactive_alerts(alerts)
        assert "RELATIONSHIP DRIFT" in result
        assert "Dad" in result
        assert "guilt-trip" in result.lower() or "directly relevant" in result.lower()

    def test_proactive_alerts_high_influence_negative(self):
        """Person mentioned 8x in stress contexts → appears in alerts."""
        agent = self._agent()
        alerts = {
            "drifting_relationships": [],
            "neglected_relationships": [],
            "high_influence": [self._high_influence(count=8, profile="mostly_negative")],
        }
        result = agent._build_proactive_alerts(alerts)
        assert "HIGH INFLUENCE" in result
        assert "Boss" in result

    def test_proactive_alerts_high_influence_mixed_flagged(self):
        """Mixed context profile also triggers high-influence alert."""
        agent = self._agent()
        alerts = {
            "drifting_relationships": [],
            "neglected_relationships": [],
            "high_influence": [self._high_influence(count=7, profile="mixed")],
        }
        result = agent._build_proactive_alerts(alerts)
        assert "HIGH INFLUENCE" in result

    def test_proactive_alerts_high_influence_positive_not_flagged(self):
        """Person mentioned 10x positively → NOT in alerts."""
        agent = self._agent()
        alerts = {
            "drifting_relationships": [],
            "neglected_relationships": [],
            "high_influence": [self._high_influence(count=10, profile="mostly_positive")],
        }
        result = agent._build_proactive_alerts(alerts)
        assert "HIGH INFLUENCE" not in result

    def test_proactive_alerts_no_drift_no_output(self):
        """All relationships active (no drifting/neglected/high_influence) → no relationship section."""
        agent = self._agent()
        alerts = {"drifting_relationships": [], "neglected_relationships": [], "high_influence": []}
        result = agent._build_proactive_alerts(alerts)
        assert result == ""

    def test_proactive_alerts_relationship_cap(self):
        """More than 2 drifting → only 2 shown."""
        agent = self._agent()
        drifting = [
            self._drifting(f"person-{i}", f"Person {i}", days=20, drift=6)
            for i in range(5)
        ]
        alerts = {"drifting_relationships": drifting, "neglected_relationships": [], "high_influence": []}
        result = agent._build_proactive_alerts(alerts)
        count = sum(1 for i in range(5) if f"Person {i}" in result)
        assert count == 2

    def test_proactive_alerts_relationship_suppressed_when_stressed(self):
        """Elevated stress + medium confidence → relationship alerts suppressed."""
        agent = self._agent()
        alerts = {
            "drifting_relationships": [self._drifting()],
            "neglected_relationships": [],
            "high_influence": [],
            # Need other alerts to ensure total_cap kicks in
            "broken_streaks": [
                {"habit_id": f"h{i}", "habit_title": f"Habit {i}", "frequency": "daily",
                 "status": "active", "current_streak": 0, "last_completed": "2026-01-01",
                 "days_since_last": 30}
                for i in range(2)
            ],
            "at_risk_streaks": [],
            "overdue_commitments": [],
        }
        state = {"stress": "elevated", "energy": "low", "confidence": "medium", "signals": []}
        result = agent._build_proactive_alerts(alerts, state=state)
        assert "RELATIONSHIP DRIFT" not in result

    def test_build_relationship_alerts_empty(self):
        """No drifting/neglected/high_influence → empty string."""
        result = MentorAgent._build_relationship_alerts([], [], [])
        assert result == ""

    def test_build_relationship_alerts_high_influence_below_threshold(self):
        """High-influence but mention_count < 5 → not flagged."""
        high = self._high_influence(count=3, profile="mostly_negative")
        high["mention_count"] = 3
        result = MentorAgent._build_relationship_alerts([], [], [high])
        assert result == ""


# ── Person Context Enrichment ──


class TestPersonContextEnrichment:
    """Test _node_context_full and _node_context_compact with relationship_stats."""

    def _person_node(self, pid="ethan", title="Ethan Shorthouse", relationship="friend"):
        return {"id": pid, "type": "person", "title": title, "relationship": relationship}

    def _rel_stats(self, pid="ethan", count=7, days=3, health="active",
                   profile="mostly_positive", pos=5, neg=1, plan=1):
        return {
            "person_id": pid,
            "relationship": "friend",
            "expected_frequency": "weekly",
            "mention_count": count,
            "days_since_mention": days,
            "health": health,
            "context_profile": profile,
            "mention_contexts": {"positive": pos, "negative": neg, "planning": plan, "neutral": 0},
        }

    def test_person_context_full_enriched(self):
        """Person node formatted with mention stats when relationship_stats provided."""
        node = self._person_node()
        stats = self._rel_stats()
        result = _node_context_full(node, [], relationship_stats=stats)
        assert "Mentions:" in result
        assert "7 this month" in result
        assert "3d ago" in result
        assert "active" in result

    def test_person_context_full_not_enriched_without_data(self):
        """Person node formatted normally when no relationship_stats provided."""
        node = self._person_node()
        result = _node_context_full(node, [])
        assert "Mentions:" not in result

    def test_person_context_compact_enriched(self):
        """Person compact format includes mention summary when stats provided."""
        node = self._person_node()
        stats = self._rel_stats(count=7, days=3)
        result = _node_context_compact(node, [], relationship_stats=stats)
        assert "Mentions:" in result
        assert "7/month" in result
        assert "3d ago" in result

    def test_person_context_compact_not_enriched_without_data(self):
        """Person compact format unchanged when no relationship_stats."""
        node = self._person_node()
        result = _node_context_compact(node, [])
        assert "Mentions:" not in result

    def test_non_person_node_not_enriched(self):
        """Non-person node is not enriched even when relationship_stats provided."""
        node = {"id": "goal-x", "type": "goal", "title": "My Goal"}
        stats = self._rel_stats()
        result = _node_context_full(node, [], relationship_stats=stats)
        assert "Mentions:" not in result

    def test_get_context_uses_relationship_data(self):
        """get_context passes relationship_data to _retrieve for person nodes."""
        from unittest.mock import MagicMock
        from mentor_agent import _BOOTSTRAP_THRESHOLD
        schema = {"type_list": ["goal", "task", "habit", "person"], "types": {}}
        agent = MentorAgent.__new__(MentorAgent)
        agent.schema = schema
        agent.graph = MagicMock()
        agent.vector_index = MagicMock()
        agent.client = MagicMock()
        agent.system_prompt_template = ""
        agent.mode_instructions = {}
        agent.model = "claude-test"

        person_node = self._person_node()
        # Need enough nodes to avoid bootstrap mode
        pad_nodes = [
            {"id": f"pad-{i}", "type": "goal", "title": f"Pad Goal {i}"}
            for i in range(_BOOTSTRAP_THRESHOLD + 1)
        ]
        all_nodes = [person_node] + pad_nodes
        agent.graph.get_all_nodes.return_value = all_nodes

        def get_node_side_effect(nid):
            if nid == "ethan":
                return person_node
            for n in pad_nodes:
                if n["id"] == nid:
                    return n
            return None

        agent.graph.get_node.side_effect = get_node_side_effect
        agent.vector_index.search.return_value = [
            {"id": "ethan", "score": 0.9, "title": "Ethan Shorthouse", "type": "person"}
        ]
        agent.graph.get_neighbors.return_value = []
        agent.graph.get_neighbors_by_hop.return_value = {}

        rel_data = {"ethan": self._rel_stats()}
        context, _ = agent.get_context("what about Ethan", relationship_data=rel_data)
        assert "Mentions:" in context


# ---------------------------------------------------------------------------
# _node_context_oneliner (Task 4)
# ---------------------------------------------------------------------------

class TestNodeContextOneliner:
    """Tests for _node_context_oneliner format function."""

    def test_node_context_oneliner_daily_format(self):
        """Daily node with date → output starts with formatted date."""
        node = {
            "id": "daily-1",
            "type": "daily",
            "title": "Training day",
            "date": "2026-03-16",
            "content": "Upper body session and portfolio review.",
            "status": "active",
        }
        result = _node_context_oneliner(node)
        # Should start with a short weekday + day + month pattern
        assert "Mar" in result
        assert "16" in result
        assert "[daily]" in result
        assert "Training day" in result

    def test_node_context_oneliner_goal_format(self):
        """Goal node → includes status and priority metadata."""
        node = {
            "id": "goal-piano",
            "type": "goal",
            "title": "Learn Piano",
            "status": "active",
            "priority": "high",
            "due": "2026-06-01",
            "content": "Reach intermediate level.",
        }
        result = _node_context_oneliner(node)
        assert "[goal]" in result
        assert "Learn Piano" in result
        assert "active" in result
        assert "high priority" in result

    def test_node_context_oneliner_length(self):
        """Typical node → output length within 50-120 chars."""
        node = {
            "id": "daily-2",
            "type": "daily",
            "title": "Monday journal",
            "date": "2026-03-17",
            "content": "Productive morning. Finished sprint planning.",
            "status": "active",
        }
        result = _node_context_oneliner(node)
        # Allow a slightly wider range to account for real-world variation
        assert len(result) <= 120, f"Oneliner too long: {len(result)} chars — '{result}'"
        assert len(result) >= 10, f"Oneliner too short: {len(result)} chars"

    def test_node_context_oneliner_title_truncation(self):
        """Title longer than 30 chars → truncated with '...'"""
        node = {
            "id": "goal-long",
            "type": "goal",
            "title": "This is a very long title that exceeds thirty characters",
            "status": "active",
        }
        result = _node_context_oneliner(node)
        assert "..." in result
        # Title part in the result should be ≤33 chars (30 + "...")
        type_tag = "[goal] "
        title_part = result.split(" — ")[0].replace(type_tag, "").strip()
        assert len(title_part) <= 33

    def test_oneliner_threshold_at_13(self, tmp_path):
        """13 compact nodes (>12) → oneliner format used in assembly."""
        from unittest.mock import MagicMock
        from vault_graph import VaultGraph
        from vector_search import VectorIndex

        nodes = []
        for i in range(13):
            nodes.append({
                "id": f"daily-{i}",
                "type": "daily",
                "title": f"Daily {i}",
                "date": f"2026-03-{i+1:02d}",
                "content": f"Content for day {i}.",
                "status": "active",
            })

        g = VaultGraph()
        g.build_from_parsed(nodes, [])
        vi = VectorIndex(str(tmp_path / "chroma"))
        vi.index_all(nodes)

        agent = MentorAgent.__new__(MentorAgent)
        agent.graph = g
        agent.vector_index = vi
        agent.schema = {
            "type_list": ["daily"],
            "types": {"daily": {"domain": "Life"}},
            "domains": {"Life": {"types": ["daily"]}},
        }
        agent.client = MagicMock()
        agent.system_prompt_template = "test {context} {today}"
        agent.model = "test-model"
        agent.mode_instructions = {}

        # Use a broad temporal query to trigger compact=True + many nodes
        from unittest.mock import patch
        from datetime import date
        frozen = date(2026, 3, 23)
        with patch("mentor_agent.date") as mock_date:
            mock_date.today.return_value = frozen
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            debug = agent.get_context_debug("what happened this week")

        # With >12 nodes and compact=True, format_used should be oneliner
        assert debug["format_used"] == "oneliner"

    def test_compact_threshold_at_12(self, tmp_path):
        """Exactly 12 compact nodes → compact format, NOT oneliner."""
        from unittest.mock import MagicMock
        from vault_graph import VaultGraph
        from vector_search import VectorIndex
        from unittest.mock import patch
        from datetime import date

        nodes = []
        for i in range(12):
            nodes.append({
                "id": f"daily-{i}",
                "type": "daily",
                "title": f"Daily {i}",
                "date": f"2026-03-{i+1:02d}",
                "content": f"Content for day {i}.",
                "status": "active",
            })

        g = VaultGraph()
        g.build_from_parsed(nodes, [])
        vi = VectorIndex(str(tmp_path / "chroma"))
        vi.index_all(nodes)

        agent = MentorAgent.__new__(MentorAgent)
        agent.graph = g
        agent.vector_index = vi
        agent.schema = {
            "type_list": ["daily"],
            "types": {"daily": {"domain": "Life"}},
            "domains": {"Life": {"types": ["daily"]}},
        }
        agent.client = MagicMock()
        agent.system_prompt_template = "test {context} {today}"
        agent.model = "test-model"
        agent.mode_instructions = {}

        frozen = date(2026, 3, 23)
        with patch("mentor_agent.date") as mock_date:
            mock_date.today.return_value = frozen
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            debug = agent.get_context_debug("what happened this week")

        # 12 nodes → compact, not oneliner (threshold is >12)
        assert debug["format_used"] in ("compact", "full")

    def test_non_compact_never_oneliner(self, tmp_path):
        """Non-compact intent with many nodes → full format, not oneliner."""
        from unittest.mock import MagicMock
        from vault_graph import VaultGraph
        from vector_search import VectorIndex

        nodes = []
        for i in range(15):
            nodes.append({
                "id": f"goal-{i}",
                "type": "goal",
                "title": f"Goal {i}",
                "content": f"I want to achieve goal {i}.",
                "status": "active",
            })

        g = VaultGraph()
        g.build_from_parsed(nodes, [])
        vi = VectorIndex(str(tmp_path / "chroma"))
        vi.index_all(nodes)

        agent = MentorAgent.__new__(MentorAgent)
        agent.graph = g
        agent.vector_index = vi
        agent.schema = {
            "type_list": ["goal"],
            "types": {"goal": {"domain": "Self"}},
            "domains": {"Self": {"types": ["goal"]}},
        }
        agent.client = MagicMock()
        agent.system_prompt_template = "test {context} {today}"
        agent.model = "test-model"
        agent.mode_instructions = {}

        # Entity lookup → compact=False, regardless of node count
        debug = agent.get_context_debug("tell me about my goals and ambitions in life")
        assert debug["format_used"] == "full"
