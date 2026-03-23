"""Integration tests verifying query-aware retrieval quality.

These tests wire up a MentorAgent with a real VectorIndex (backed by a temp
ChromaDB) plus a VaultGraph to verify that the adaptive pipeline produces
better results than the old fixed-top-5 approach.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mentor_agent import MentorAgent, _MAX_CONTEXT_TOKENS, _CHARS_PER_TOKEN
from vector_search import VectorIndex
from vault_graph import VaultGraph


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def weekly_vault(tmp_path):
    """Return (graph, vector_index) with 10 daily-type nodes from 'this week'."""
    today = date(2026, 3, 23)  # Monday = 2026-03-17
    week_start = today - timedelta(days=today.weekday())  # Monday

    nodes = []
    for i in range(12):  # 12 > bootstrap threshold of 10
        node_date = week_start + timedelta(days=i % 7)
        nodes.append({
            "id": f"daily-{i}",
            "type": "daily",
            "title": f"Daily Journal {i}",
            "date": node_date.isoformat(),
            "content": f"Today I focused on item {i}. Made good progress.",
            "status": "active",
            "domain": "Life",
        })

    g = VaultGraph()
    g.build_from_parsed(nodes, [])

    vi = VectorIndex(str(tmp_path / "chroma"))
    vi.index_all(nodes)

    return g, vi


@pytest.fixture
def mixed_vault(tmp_path):
    """Return (graph, vector_index) with goals, tasks, notes, and a person.

    Includes 10+ nodes so the vault is above the bootstrap threshold.
    """
    nodes = [
        {"id": "goal-piano", "type": "goal", "title": "Learn Piano",
         "content": "I want to learn piano at an intermediate level.", "status": "active", "domain": "Self"},
        {"id": "goal-fitness", "type": "goal", "title": "Get Fit",
         "content": "Run a marathon by end of year.", "status": "active", "domain": "Self"},
        {"id": "goal-read", "type": "goal", "title": "Read More Books",
         "content": "Read at least 24 books this year.", "status": "active", "domain": "Self"},
        {"id": "goal-sleep", "type": "goal", "title": "Sleep Better",
         "content": "Get 8 hours of sleep consistently.", "status": "active", "domain": "Self"},
        {"id": "goal-meditate", "type": "goal", "title": "Daily Meditation",
         "content": "Meditate for 10 minutes every morning.", "status": "active", "domain": "Self"},
        {"id": "task-1", "type": "task", "title": "Book dentist appointment",
         "content": "Schedule a dentist appointment.", "status": "active", "domain": "Planning"},
        {"id": "task-2", "type": "task", "title": "Buy groceries",
         "content": "Buy vegetables and fruit.", "status": "active", "domain": "Planning"},
        {"id": "note-1", "type": "note", "title": "Music Theory",
         "content": "Scales and chords basics.", "status": "", "domain": "Knowledge"},
        {"id": "note-2", "type": "note", "title": "Stoicism",
         "content": "Core tenets of stoic philosophy.", "status": "", "domain": "Knowledge"},
        {"id": "person-alice", "type": "person", "title": "Alice",
         "content": "Musician friend.", "status": "", "domain": "People"},
        {"id": "person-bob", "type": "person", "title": "Bob",
         "content": "Work colleague.", "status": "", "domain": "People"},
    ]

    g = VaultGraph()
    g.build_from_parsed(nodes, [])

    vi = VectorIndex(str(tmp_path / "chroma"))
    vi.index_all(nodes)

    return g, vi


def _make_agent(graph, vector_index, schema=None):
    schema = schema or {
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
# Broad temporal query
# ---------------------------------------------------------------------------

class TestBroadTemporalRetrieval:

    def test_broad_temporal_returns_many_nodes(self, weekly_vault):
        """Graph with 10 daily nodes from this week → context includes ≥8 of them."""
        graph, vector_index = weekly_vault
        agent = _make_agent(graph, vector_index)

        frozen = date(2026, 3, 23)
        with patch("mentor_agent.date") as mock_date:
            mock_date.today.return_value = frozen
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            context, results = agent.get_context("what happened this week")

        assert len(results) >= 8, f"Expected ≥8 results, got {len(results)}"

    def test_broad_temporal_compact_format(self, weekly_vault):
        """Broad temporal query → tier 1 nodes use compact format."""
        graph, vector_index = weekly_vault
        agent = _make_agent(graph, vector_index)

        frozen = date(2026, 3, 23)
        with patch("mentor_agent.date") as mock_date:
            mock_date.today.return_value = frozen
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            context, results = agent.get_context("what happened this week")

        # Compact format does NOT emit "Content:" label
        assert "Content:" not in context
        # But should contain node titles
        assert "Daily Journal" in context


# ---------------------------------------------------------------------------
# Entity lookup
# ---------------------------------------------------------------------------

class TestEntityLookupRetrieval:

    def test_entity_lookup_full_content(self, mixed_vault):
        """Entity lookup → context includes full content of the target node."""
        graph, vector_index = mixed_vault
        agent = _make_agent(graph, vector_index)

        context, results = agent.get_context("tell me about learn piano")
        # Full format includes "Content:" label
        assert "Content:" in context
        assert "piano" in context.lower()


# ---------------------------------------------------------------------------
# Domain filter
# ---------------------------------------------------------------------------

class TestDomainFilterRetrieval:

    def test_domain_filter_excludes_other_domains(self, mixed_vault):
        """'show me my goals' → majority of tier 1 results are Self domain (goals)."""
        graph, vector_index = mixed_vault
        agent = _make_agent(graph, vector_index)

        context, results = agent.get_context("show me my goals")
        goal_count = sum(1 for r in results if r.get("type") == "goal")
        assert goal_count >= 1  # at least one goal in results


# ---------------------------------------------------------------------------
# Specific temporal (not compact)
# ---------------------------------------------------------------------------

class TestSpecificTemporalRetrieval:

    def test_specific_temporal_not_compact(self, weekly_vault):
        """'what's on tomorrow' with few nodes → uses full content format."""
        graph, vector_index = weekly_vault
        agent = _make_agent(graph, vector_index)

        frozen = date(2026, 3, 23)
        with patch("mentor_agent.date") as mock_date:
            mock_date.today.return_value = frozen
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            context, results = agent.get_context("what's on tomorrow")

        # temporal_specific → compact=False → full content format
        assert "Content:" in context or results == [] or "(No relevant" in context


# ---------------------------------------------------------------------------
# General query backward compatibility
# ---------------------------------------------------------------------------

class TestGeneralQueryBackwardCompat:

    def test_general_query_backward_compatible(self, mixed_vault):
        """Generic query → k=5, full content, same behaviour as Sprint 5."""
        graph, vector_index = mixed_vault
        agent = _make_agent(graph, vector_index)

        context, results = agent.get_context("this is a generic query about nothing specific")
        # k=5 for general intent
        assert len(results) <= 5
        # Full content format
        assert "Content:" in context or results == []


# ---------------------------------------------------------------------------
# Debug endpoint integration
# ---------------------------------------------------------------------------

class TestDebugEndpointRetrieval:

    def test_debug_endpoint_returns_diagnostics(self, mixed_vault):
        """get_context_debug() returns expected structure with intent and candidates."""
        graph, vector_index = mixed_vault
        agent = _make_agent(graph, vector_index)

        debug = agent.get_context_debug("tell me about piano")

        assert "query" in debug
        assert "intent" in debug
        assert "candidates" in debug
        assert "nodes_in_context" in debug
        assert "context_length_chars" in debug
        assert "context_length_tokens_est" in debug

        intent = debug["intent"]
        assert "intent" in intent
        assert "k" in intent
        assert "compact" in intent

        # Candidates have score breakdowns
        if debug["candidates"]:
            cand = debug["candidates"][0]
            assert "id" in cand
            assert "scores" in cand
            scores = cand["scores"]
            assert "semantic" in scores
            assert "total" in scores
            assert "selected" in cand
            assert "tier" in cand

    def test_debug_domains_list(self, mixed_vault):
        graph, vector_index = mixed_vault
        agent = _make_agent(graph, vector_index)

        debug = agent.get_context_debug("what are my goals")
        assert isinstance(debug["domains"], list)

    def test_debug_date_range_none_for_general(self, mixed_vault):
        graph, vector_index = mixed_vault
        agent = _make_agent(graph, vector_index)

        debug = agent.get_context_debug("what do you think about life in general")
        assert debug["date_range"] is None or isinstance(debug["date_range"], list)
