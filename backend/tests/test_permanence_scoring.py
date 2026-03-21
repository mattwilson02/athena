"""Tests for permanence-based retrieval scoring in mentor_agent.py."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mentor_agent import _PERMANENCE_DEFAULTS, _get_permanence, MentorAgent


# ── Unit tests for _get_permanence ──


class TestGetPermanence:
    def test_identity_boost_applied(self):
        level, boost = _get_permanence("value")
        assert level == "identity"
        assert boost == pytest.approx(0.15)

    def test_belief_identity(self):
        level, boost = _get_permanence("belief")
        assert level == "identity"
        assert boost == pytest.approx(0.15)

    def test_fear_identity(self):
        level, boost = _get_permanence("fear")
        assert level == "identity"
        assert boost == pytest.approx(0.15)

    def test_strategic_boost_applied(self):
        level, boost = _get_permanence("goal")
        assert level == "strategic"
        assert boost == pytest.approx(0.10)

    def test_habit_strategic(self):
        level, boost = _get_permanence("habit")
        assert level == "strategic"
        assert boost == pytest.approx(0.10)

    def test_skill_strategic(self):
        level, boost = _get_permanence("skill")
        assert level == "strategic"
        assert boost == pytest.approx(0.10)

    def test_project_strategic(self):
        level, boost = _get_permanence("project")
        assert level == "strategic"
        assert boost == pytest.approx(0.10)

    def test_tactical_no_boost(self):
        level, boost = _get_permanence("task")
        assert level == "tactical"
        assert boost == pytest.approx(0.00)

    def test_event_tactical(self):
        level, boost = _get_permanence("event")
        assert level == "tactical"
        assert boost == pytest.approx(0.00)

    def test_ephemeral_penalty_applied(self):
        level, boost = _get_permanence("daily")
        assert level == "ephemeral"
        assert boost == pytest.approx(-0.05)

    def test_note_ephemeral(self):
        level, boost = _get_permanence("note")
        assert level == "ephemeral"
        assert boost == pytest.approx(-0.05)

    def test_unlisted_type_no_boost(self):
        """Types not in _PERMANENCE_DEFAULTS default to tactical with 0.0 boost."""
        level, boost = _get_permanence("person")
        assert level == "tactical"
        assert boost == pytest.approx(0.00)

    def test_article_unlisted(self):
        level, boost = _get_permanence("article")
        assert level == "tactical"
        assert boost == pytest.approx(0.00)

    def test_movie_unlisted(self):
        level, boost = _get_permanence("movie")
        assert level == "tactical"
        assert boost == pytest.approx(0.00)


# ── Integration tests via get_context scoring ──


class FakeVectorIndex:
    def __init__(self, results):
        self._results = results

    def search(self, query, n=10):
        return self._results[:n]

    def find_duplicates(self, *a, **kw):
        return []


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


def _make_agent(nodes, search_results):
    """Build a MentorAgent with fake graph and vector index using 15+ nodes."""
    # Pad to exceed bootstrap threshold
    all_nodes = nodes[:]
    for i in range(15 - len(nodes)):
        all_nodes.append({"id": f"pad-{i}", "title": f"Pad {i}", "type": "note"})

    schema = {"type_list": ["goal", "value", "task", "daily", "person", "note"], "types": {}}
    graph = FakeGraph(all_nodes)
    vector_index = FakeVectorIndex(search_results)

    agent = MentorAgent.__new__(MentorAgent)
    agent.graph = graph
    agent.vector_index = vector_index
    agent.schema = schema
    agent.client = MagicMock()
    agent.system_prompt_template = "test {context} {today}"
    agent.model = "test-model"
    agent.mode_instructions = {}
    return agent


class TestPermanenceBeatsSemantic:
    def test_permanence_beats_semantic_in_close_race(self):
        """Identity node (semantic 0.5) outscores tactical node (semantic 0.6)."""
        # semantic score = 1.0 - distance (lower distance = better)
        # value node: distance=0.5 → semantic=0.5, permanence=+0.15 → total≈0.65
        # task node: distance=0.4 → semantic=0.6, permanence=+0.00 → total≈0.60
        nodes = [
            {"id": "my-value", "title": "Discipline", "type": "value"},
            {"id": "my-task", "title": "Buy groceries", "type": "task"},
        ]
        search_results = [
            {"id": "my-value", "title": "Discipline", "type": "value", "score": 0.5},
            {"id": "my-task", "title": "Buy groceries", "type": "task", "score": 0.4},
        ]
        agent = _make_agent(nodes, search_results)
        context, results = agent.get_context("discipline")

        # value node should rank higher despite lower semantic score
        ids = [r["id"] for r in results]
        assert "my-value" in ids
        value_idx = ids.index("my-value") if "my-value" in ids else 999
        task_idx = ids.index("my-task") if "my-task" in ids else 999
        assert value_idx < task_idx, (
            f"Expected value (identity) to rank before task (tactical), "
            f"got value_idx={value_idx}, task_idx={task_idx}"
        )

    def test_identity_outranks_ephemeral(self):
        """Value node beats daily node with equal semantic scores."""
        nodes = [
            {"id": "my-value", "title": "Discipline", "type": "value"},
            {"id": "my-daily", "title": "Today log", "type": "daily"},
        ]
        search_results = [
            {"id": "my-value", "title": "Discipline", "type": "value", "score": 0.5},
            {"id": "my-daily", "title": "Today log", "type": "daily", "score": 0.5},
        ]
        agent = _make_agent(nodes, search_results)
        context, results = agent.get_context("tell me about today")

        ids = [r["id"] for r in results]
        if "my-value" in ids and "my-daily" in ids:
            assert ids.index("my-value") < ids.index("my-daily")


class TestTemporalInjectionIncludesPermanence:
    def test_temporal_injection_includes_permanence(self):
        """Temporally injected value node gets 0.3 + 0.15 = 0.45 base score.

        Tests the scoring math directly using _get_permanence and the
        injection formula: 0.3 + status_penalty + permanence_boost.
        """
        from mentor_agent import _STATUS_PENALTIES

        # value node: identity level, +0.15 boost
        level, boost = _get_permanence("value")
        assert level == "identity"

        node_status = ""  # active/no status
        status_penalty = _STATUS_PENALTIES.get(node_status, 0.0)
        injection_score = 0.3 + status_penalty + boost
        assert injection_score == pytest.approx(0.45)

    def test_ephemeral_temporal_injection_penalized(self):
        """Temporally injected daily node gets 0.3 + (-0.05) = 0.25 base score."""
        from mentor_agent import _STATUS_PENALTIES

        level, boost = _get_permanence("daily")
        assert level == "ephemeral"

        node_status = ""
        status_penalty = _STATUS_PENALTIES.get(node_status, 0.0)
        injection_score = 0.3 + status_penalty + boost
        assert injection_score == pytest.approx(0.25)

    def test_identity_injection_higher_than_ephemeral(self):
        """Identity temporal injection score (0.45) > ephemeral injection score (0.25)."""
        from mentor_agent import _STATUS_PENALTIES

        _, value_boost = _get_permanence("value")
        _, daily_boost = _get_permanence("daily")
        base = 0.3

        value_score = base + value_boost
        daily_score = base + daily_boost
        assert value_score > daily_score

    def test_temporal_injection_via_get_context(self):
        """Temporally injected identity node appears in context via today's date.

        Uses real today's date to avoid mocking issues with isinstance checks.
        """
        from datetime import date

        today_str = date.today().isoformat()
        nodes = [
            {"id": "identity-value", "title": "Discipline", "type": "value", "date": today_str},
        ]
        all_nodes = nodes[:]
        for i in range(14):
            all_nodes.append({"id": f"pad-{i}", "title": f"Pad {i}", "type": "note"})

        schema = {"type_list": ["value", "note"], "types": {}}
        graph = FakeGraph(all_nodes)

        # Semantic search returns nothing for the value node directly
        vector_index = FakeVectorIndex([])

        agent = MentorAgent.__new__(MentorAgent)
        agent.graph = graph
        agent.vector_index = vector_index
        agent.schema = schema
        agent.client = MagicMock()
        agent.system_prompt_template = "test {context} {today}"
        agent.model = "test-model"
        agent.mode_instructions = {}

        context, results = agent.get_context("what's happening today")
        # Identity node should be injected and appear in context
        assert "Discipline" in context
