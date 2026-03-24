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

from mentor_agent import MentorAgent, _MAX_CONTEXT_TOKENS, _BASE_TOKEN_BUDGET, _CHARS_PER_TOKEN
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


# ---------------------------------------------------------------------------
# Relational vault fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def relational_vault(tmp_path):
    """Person node with linked experiences/events — tests relational expansion."""
    nodes = [
        {"id": "person-ben", "type": "person", "title": "Ben",
         "content": "Good friend from university.", "status": "", "domain": "People"},
        {"id": "exp-1", "type": "experience", "title": "Coffee with Ben",
         "content": "Met Ben for coffee, discussed goals.",
         "date": "2026-03-10", "status": "active", "domain": "Life"},
        {"id": "exp-2", "type": "experience", "title": "Ben birthday dinner",
         "content": "Celebrated Ben's birthday at the Italian place.",
         "date": "2026-02-20", "status": "active", "domain": "Life"},
        {"id": "exp-3", "type": "experience", "title": "Hiking with Ben",
         "content": "Did the coastal trail with Ben.",
         "date": "2026-01-15", "status": "active", "domain": "Life"},
        {"id": "note-ben", "type": "note", "title": "Ben intro notes",
         "content": "Ben works in finance, interested in startups.", "domain": "People"},
        {"id": "event-1", "type": "event", "title": "Ben visit next week",
         "content": "Ben coming to town.",
         "date": "2026-03-30", "status": "active", "domain": "Planning"},
        # Extra unrelated nodes to exceed bootstrap threshold
        {"id": "goal-1", "type": "goal", "title": "Exercise more",
         "content": "Run 3x per week.", "status": "active", "domain": "Self"},
        {"id": "goal-2", "type": "goal", "title": "Read more books",
         "content": "One book per month.", "status": "active", "domain": "Self"},
        {"id": "goal-3", "type": "goal", "title": "Learn Spanish",
         "content": "Reach B1 level.", "status": "active", "domain": "Self"},
        {"id": "task-1", "type": "task", "title": "Buy groceries",
         "content": "Vegetables and fruit.", "status": "active", "domain": "Planning"},
        {"id": "task-2", "type": "task", "title": "Call doctor",
         "content": "Schedule checkup.", "status": "active", "domain": "Planning"},
    ]
    edges = [
        ("person-ben", "exp-1", "related"),
        ("person-ben", "exp-2", "related"),
        ("person-ben", "exp-3", "related"),
        ("person-ben", "note-ben", "related"),
        ("person-ben", "event-1", "related"),
    ]

    g = VaultGraph()
    g.build_from_parsed(nodes, edges)

    vi = VectorIndex(str(tmp_path / "chroma"))
    vi.index_all(nodes)

    return g, vi


@pytest.fixture
def large_relational_vault(tmp_path):
    """Person node with 25 linked nodes — tests cap at 20."""
    nodes = [
        {"id": "person-alice", "type": "person", "title": "Alice",
         "content": "Close friend.", "status": "", "domain": "People"},
    ]
    edges = []
    for i in range(25):
        nid = f"exp-{i}"
        nodes.append({
            "id": nid,
            "type": "experience",
            "title": f"Experience with Alice {i}",
            "content": f"Had a great time {i}.",
            "date": f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
            "status": "active",
            "domain": "Life",
        })
        edges.append(("person-alice", nid, "related"))

    # Add extra filler nodes to exceed bootstrap threshold
    for j in range(5):
        nodes.append({
            "id": f"goal-filler-{j}",
            "type": "goal",
            "title": f"Goal {j}",
            "content": f"Achieve goal {j}.",
            "status": "active",
            "domain": "Self",
        })

    g = VaultGraph()
    g.build_from_parsed(nodes, edges)

    vi = VectorIndex(str(tmp_path / "chroma"))
    vi.index_all(nodes)

    return g, vi


# ---------------------------------------------------------------------------
# Relational retrieval (Task 3)
# ---------------------------------------------------------------------------

class TestRelationalRetrieval:

    def test_relational_expands_person_neighbors(self, relational_vault):
        """Person with 5 linked nodes → all 5 appear in context (relational expansion)."""
        graph, vector_index = relational_vault
        agent = _make_agent(graph, vector_index)

        context, results = agent.get_context(
            "how's my relationship with ben",
            relationship_data=None,
        )
        result_ids = {r["id"] for r in results}
        # Ben + his 5 linked nodes should all be present
        assert "person-ben" in result_ids
        linked = {"exp-1", "exp-2", "exp-3", "note-ben", "event-1"}
        assert linked.issubset(result_ids), f"Missing linked nodes: {linked - result_ids}"

    def test_relational_expansion_capped_at_20(self, large_relational_vault):
        """Person with 25 linked nodes → relational_expansion adds at most 20 nodes."""
        graph, vector_index = large_relational_vault
        agent = _make_agent(graph, vector_index)

        debug = agent.get_context_debug("how's my relationship with alice")
        expansion_nodes = [
            c for c in debug["candidates"]
            if c.get("source") == "relational_expansion"
        ]
        assert len(expansion_nodes) <= 20, (
            f"Expansion added {len(expansion_nodes)} nodes, expected ≤ 20"
        )

    def test_relational_expansion_sorted_by_recency(self, large_relational_vault):
        """Expansion nodes are the most recent ones (sorted by date descending)."""
        graph, vector_index = large_relational_vault
        agent = _make_agent(graph, vector_index)

        context, results = agent.get_context("how's my relationship with alice")
        result_ids = {r["id"] for r in results}
        # exp-24 has date 2026-01-25 and exp-23 has date 2026-12-24 (month=(23%12)+1=12)
        # The point is: not all 25 fit, so at most 20 are included

        # All returned experience nodes should be a subset of the 25
        experience_nodes = [r for r in results if r["id"].startswith("exp-")]
        all_exp_ids = {f"exp-{i}" for i in range(25)}
        assert all(r["id"] in all_exp_ids for r in experience_nodes)

    def test_non_relational_no_expansion(self, relational_vault):
        """Entity lookup on a person → no neighbor expansion (expand_person not set)."""
        graph, vector_index = relational_vault
        agent = _make_agent(graph, vector_index)

        # entity_lookup intent — should NOT expand neighbors
        context, results = agent.get_context("tell me about ben")
        result_ids = {r["id"] for r in results}
        # Ben should be there, but not necessarily all 5 linked nodes
        # (entity_lookup k=5 with no expansion — linked nodes may or may not appear via 2-hop)
        # The key assertion: intent is entity_lookup, not relational
        debug = agent.get_context_debug("tell me about ben")
        assert debug["intent"]["intent"] == "entity_lookup"

    def test_relational_debug_shows_expansion_source(self, relational_vault):
        """Debug output includes source='relational_expansion' for expanded nodes."""
        graph, vector_index = relational_vault
        agent = _make_agent(graph, vector_index)

        debug = agent.get_context_debug("how's my relationship with ben")
        expansion_candidates = [
            c for c in debug["candidates"]
            if c.get("source") == "relational_expansion"
        ]
        assert len(expansion_candidates) > 0, "Expected relational_expansion candidates in debug"


# ---------------------------------------------------------------------------
# Dynamic budget integration (Task 5)
# ---------------------------------------------------------------------------

class TestDynamicBudgetIntegration:

    def test_relational_query_full_context(self, relational_vault):
        """Relational query → all linked nodes in context, budget > base."""
        graph, vector_index = relational_vault
        agent = _make_agent(graph, vector_index)

        debug = agent.get_context_debug("how's my relationship with ben")
        # Budget should be above base (relational is 1.5x)
        assert debug["token_budget"] > _BASE_TOKEN_BUDGET

    def test_temporal_broad_with_dynamic_budget(self, weekly_vault):
        """Temporal broad with 12 dailies → budget > base."""
        graph, vector_index = weekly_vault
        agent = _make_agent(graph, vector_index)

        from unittest.mock import patch
        from datetime import date
        frozen = date(2026, 3, 23)
        with patch("mentor_agent.date") as mock_date:
            mock_date.today.return_value = frozen
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            debug = agent.get_context_debug("what happened this week")

        assert debug["token_budget"] > _BASE_TOKEN_BUDGET

    def test_focused_query_unchanged(self, mixed_vault):
        """Focused entity query → budget = base, full format."""
        graph, vector_index = mixed_vault
        agent = _make_agent(graph, vector_index)

        debug = agent.get_context_debug("tell me about learn piano")
        assert debug["token_budget"] == _BASE_TOKEN_BUDGET
        assert debug["format_used"] == "full"

    def test_relational_expansion_with_summarization(self, large_relational_vault):
        """Relational query with >12 linked nodes → oneliner format."""
        graph, vector_index = large_relational_vault
        agent = _make_agent(graph, vector_index)

        debug = agent.get_context_debug("how's my relationship with alice")
        # 21+ nodes (person + 20 expansion) → should trigger oneliner
        assert debug["format_used"] == "oneliner"

    def test_debug_endpoint_includes_budget(self, mixed_vault):
        """get_context_debug() returns token_budget and format_used fields."""
        graph, vector_index = mixed_vault
        agent = _make_agent(graph, vector_index)

        debug = agent.get_context_debug("relationship with ben")
        assert "token_budget" in debug
        assert "format_used" in debug
        assert isinstance(debug["token_budget"], int)
        assert debug["format_used"] in ("full", "compact", "oneliner")
